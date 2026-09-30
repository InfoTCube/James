"""FastAPI app: JSON endpoints + the dashboard."""

import threading
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core import tts
from assistant.core.config import fold, load_config
from assistant.core.db import CollectorRun, get_engine
from assistant.modules.alarm.routes import router as alarm_router
from assistant.modules.birthdays.routes import router as birthdays_router
from assistant.modules.calendar.routes import router as calendar_router
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.routes import router as mpk_router
from assistant.modules.weather.routes import router as weather_router

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Changes on every (re)start, i.e. every deploy: open dashboards see it and reload themselves.
VERSION = str(int(time.time()))
app = FastAPI(title="Assistant")
app.include_router(weather_router)
app.include_router(calendar_router)
app.include_router(mpk_router)
app.include_router(birthdays_router)
app.include_router(alarm_router)

# get the voice (~60 MB, once) ready before the first alarm needs it
for _voice in (load_config().voice, load_config().voice_pl):
    threading.Thread(target=tts.download_voice, args=(_voice,), daemon=True).start()


@lru_cache(maxsize=1)
def polish_lexicon(_hour: int) -> set[str]:
    """Words of Wrocław stop names (folded), rebuilt every hour: Polish for the voice."""
    with Session(get_engine()) as session:
        return {fold(w) for name in mpk.stop_names(session) for w in name.split() if len(w) > 2}


def latest_runs() -> list[CollectorRun]:
    """Most recent run of each collector."""
    latest = select(func.max(CollectorRun.id)).group_by(CollectorRun.module)
    with Session(get_engine()) as session:
        return list(
            session.scalars(
                select(CollectorRun)
                .where(CollectorRun.id.in_(latest))
                .order_by(CollectorRun.module)
            )
        )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"version": VERSION})


@app.get("/debug")
def debug(request: Request):
    return templates.TemplateResponse(
        request, "debug.html", {"runs": latest_runs(), "tz": load_config().tz}
    )


@app.get("/api/tts")
def speak(text: str = Query(max_length=500)) -> Response:
    """`text` read aloud by the configured Piper voice, as WAV (the dashboard plays it)."""
    try:
        config = load_config()
        lexicon = polish_lexicon(int(time.time() // 3600))
        wav = tts.synthesize_mixed(text, config.voice, config.voice_pl, lexicon)
    except Exception as e:  # the dashboard falls back to the browser's own voice
        raise HTTPException(503, f"speech unavailable: {type(e).__name__}") from e
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})
