"""Dashboard widget: the trip you need right now (to an event, between events, or home)."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.mpk import service
from assistant.modules.mpk.live import add_live_delays

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)

TRACKER = service.Tracker()  # vehicle movement between refreshes, for live delays


@router.get("/widgets/mpk")
def mpk_widget(request: Request):
    config, now, engine = load_config(), utcnow(), get_engine()
    with Session(engine) as session:
        trip = service.current_trip(session, now, config)
        if trip is None:
            return HTMLResponse("")  # no trip right now: the card disappears
        if trip.options:
            add_live_delays(session, trip.options, now, TRACKER)
    return templates.TemplateResponse(
        request,
        "widget.html",
        {
            "timetable_end": trip.timetable_end,
            "leg": trip.leg,
            "options": trip.options,
            "leave_by": trip.leave_by,
            "tz": config.tz,
            "stale": is_stale(engine, "mpk", timedelta(days=2)),
        },
    )
