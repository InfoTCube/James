"""Dashboard widget: rest of today + tomorrow."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.calendar import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/calendar")
def calendar_widget(request: Request):
    tz, now, engine = load_config().tz, utcnow(), get_engine()
    with Session(engine) as session:
        today = [e for e in service.day_events(session, now, tz) if e.end > now]
        tomorrow = service.day_events(session, now + timedelta(days=1), tz)
    return templates.TemplateResponse(
        request,
        "widget.html",
        {
            "days": [("Today", today), ("Tomorrow", tomorrow)],
            "stale": is_stale(engine, "calendar", timedelta(hours=1)),
            "tz": tz,
        },
    )
