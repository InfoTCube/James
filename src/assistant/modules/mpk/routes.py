"""Dashboard widget: the trip you need right now (to an event, between events, or home)."""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.mpk import client, service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)

TRACKER = service.Tracker()  # vehicle movement between refreshes, for live delays


def add_live_delays(session: Session, options: list[service.Connection], now: datetime) -> None:
    """Fill in delay_min from live vehicle positions (best effort, never raises)."""
    trams = {c.line for c in options if c.is_tram}
    buses = {c.line for c in options if not c.is_tram}
    vehicles = service.parse_positions(client.vehicle_positions(trams, buses))
    TRACKER.update(vehicles, now)
    for c in options:
        stops = service.trip_stops_until(session, c.trip_id, c.from_seq)
        if stops:
            midnight = c.departs - timedelta(seconds=stops[-1][2])
            line_vehicles = [v for v in vehicles if v.line == c.line]
            c.delay_min = service.estimate_delay(stops, midnight, line_vehicles, TRACKER, now)


@router.get("/widgets/mpk")
def mpk_widget(request: Request):
    config, now, engine = load_config(), utcnow(), get_engine()
    with Session(engine) as session:
        trip = service.current_trip(session, now, config)
        if trip is None:
            return HTMLResponse("")  # no trip right now: the card disappears
        if trip.options:
            add_live_delays(session, trip.options, now)
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
