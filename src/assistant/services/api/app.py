"""FastAPI app: JSON endpoints + the dashboard."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import CollectorRun, get_engine

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app = FastAPI(title="Assistant")


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
    return templates.TemplateResponse(
        request, "dashboard.html", {"runs": latest_runs(), "tz": load_config().tz}
    )
