"""FastAPI app: JSON endpoints + the dashboard."""

import threading
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core import tts
from assistant.core.collector import latest_runs
from assistant.core.config import fold, load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.alarm.routes import router as alarm_router
from assistant.modules.biedronka.routes import router as biedronka_router
from assistant.modules.birthdays.routes import router as birthdays_router
from assistant.modules.books.routes import router as books_router
from assistant.modules.calendar.routes import router as calendar_router
from assistant.modules.events.routes import router as events_router
from assistant.modules.films.routes import router as films_router
from assistant.modules.flights.routes import router as flights_router
from assistant.modules.jobs.routes import router as jobs_router
from assistant.modules.meals.routes import router as meals_router
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.routes import router as mpk_router
from assistant.modules.music.routes import router as music_router
from assistant.modules.news.routes import router as news_router
from assistant.modules.parcels.routes import router as parcels_router
from assistant.modules.shopping.routes import router as shopping_router
from assistant.modules.sports.routes import router as sports_router
from assistant.modules.timers.routes import router as timers_router
from assistant.modules.weather.routes import router as weather_router
from assistant.services.bot.replies import reply_status
from assistant.services.voice.routes import router as voice_router

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# Changes on every (re)start, i.e. every deploy: open dashboards see it and reload themselves.
VERSION = str(int(time.time()))
app = FastAPI(title="Assistant")
app.include_router(weather_router)
app.include_router(calendar_router)
app.include_router(mpk_router)
app.include_router(birthdays_router)
app.include_router(alarm_router)
app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(flights_router)
app.include_router(news_router)
app.include_router(films_router)
app.include_router(books_router)
app.include_router(sports_router)
app.include_router(biedronka_router)
app.include_router(music_router)
app.include_router(parcels_router)
app.include_router(shopping_router)
app.include_router(timers_router)
app.include_router(meals_router)
app.include_router(voice_router)

# get the voice (~60 MB, once) ready before the first alarm needs it
for _voice in (load_config().voice, load_config().voice_pl):
    threading.Thread(target=tts.download_voice, args=(_voice,), daemon=True).start()


@lru_cache(maxsize=1)
def polish_lexicon(_hour: int) -> set[str]:
    """Words of Wrocław stop names (folded), rebuilt every hour: Polish for the voice."""
    with Session(get_engine()) as session:
        return {fold(w) for name in mpk.stop_names(session) for w in name.split() if len(w) > 2}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/")
def dashboard(request: Request):
    screen = load_config().screen
    off = screen.dark_from.strftime("%H:%M") if screen.dark_from else ""
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"version": VERSION, "screen_off": off, "screen_on": screen.dark_until.strftime("%H:%M")},
    )


@app.get("/debug")
def debug(request: Request):
    config = load_config()
    with Session(get_engine()) as session:
        status = reply_status(session, utcnow(), config, [])
        runs = latest_runs(session)
    return templates.TemplateResponse(
        request, "debug.html", {"runs": runs, "status": status, "tz": config.tz}
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
