"""Dashboard widget: the trip you need right now (to an event, between events, or home)."""

from datetime import timedelta
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.mpk import service
from assistant.modules.mpk.alerts import leave_now_message
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
            "late_min": trip.late_min,
            "tz": config.tz,
            "stale": is_stale(engine, "mpk", timedelta(days=2)),
        },
    )


@router.get("/widgets/mpk/strip")
def mpk_strip():
    """One line for the dashboard's urgent strip while a trip is on, else nothing."""
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        trip = service.current_trip(session, now, config)
        if trip is None or not trip.options:
            return HTMLResponse("")
        add_live_delays(session, trip.options, now, TRACKER)
    c, tz = trip.options[0], config.tz
    vehicle = f"{'🚋' if c.is_tram else '🚌'} {c.line} at {c.departs.astimezone(tz):%H:%M}"
    if c.delay_min:
        vehicle += f" ~{c.delay_min:+d} min"
    if trip.late_min:
        leave = f"{trip.leave_by.astimezone(tz):%H:%M}"
        head = f"~{trip.late_min} min late for {trip.leg.title}: leave by {leave}"
    elif trip.leave_by:
        head = f"Leave by {trip.leave_by.astimezone(tz):%H:%M} for {trip.leg.title}"
    else:
        head = "Going home"
    return HTMLResponse(
        f"<b>{escape(head)}</b><span>{escape(vehicle)} from {escape(c.from_stop)}</span>"
    )


@router.get("/api/mpk/announce")
def announce() -> dict:
    """While it's time to leave (same window and live delay as the Telegram alert):
    {"key", "lines"} for the dashboard to show and read aloud once; otherwise {}."""
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        trip = service.current_trip(session, now, config)
        if trip and trip.options:
            add_live_delays(session, trip.options, now, TRACKER)
        message = leave_now_message(trip, now, config.tz)
    if message is None:
        return {}
    key, text = message
    return {"key": key, "lines": text.splitlines()}
