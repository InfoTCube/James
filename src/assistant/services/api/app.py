"""FastAPI app: JSON endpoints + the dashboard."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import CollectorRun, get_engine
from assistant.modules.weather.routes import router as weather_router

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app = FastAPI(title="Assistant")
app.include_router(weather_router)


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
    return {"status": "ok"}


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/debug")
def debug(request: Request):
    return templates.TemplateResponse(
        request, "debug.html", {"runs": latest_runs(), "tz": load_config().tz}
    )
