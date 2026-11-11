"""Timers on the dashboard: what's ringing (bell screen) and the countdowns in the rail."""

from fastapi import APIRouter
from sqlalchemy.orm import Session

from assistant.core.db import get_engine, utcnow
from assistant.modules.timers import service

router = APIRouter()


@router.get("/api/timers")
def timers_state() -> dict:
    now = utcnow()
    with Session(get_engine()) as session:
        return {
            "ringing": [{"id": t.id, "label": t.label} for t in service.ringing(session, now)],
            "running": [
                {"label": t.label, "seconds": round((t.ends_at - now).total_seconds())}
                for t in service.running(session, now)
            ],
        }


@router.post("/api/timers/stop")
def stop_timers() -> dict:
    """The Stop button: every ringing timer."""
    now = utcnow()
    with Session(get_engine()) as session, session.begin():
        if service.ringing(session, now):
            service.stop(session, now)
    return timers_state()
