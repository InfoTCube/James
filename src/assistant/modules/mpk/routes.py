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
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import client, service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)

HOME_WINDOW = timedelta(hours=2)  # how far ahead to look for a ride home
TRACKER = service.Tracker()  # vehicle movement between refreshes, for live delays


def add_live_delays(session: Session, options: list[service.Connection], now) -> None:
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
    if "home" not in config.places:
        return HTMLResponse("")
    home_place = config.places["home"]
    home = service.Target("home", home_place.stops, home_place.walk_minutes)

    with Session(engine) as session:
        events = calendar.events_between(
            session, now - timedelta(days=1), now + timedelta(days=1), config.tz
        )
        targets: dict[str, service.Target | None] = {}
        located = []
        for e in events:
            if e.location and not e.all_day:
                if e.location not in targets:
                    targets[e.location] = service.find_target(session, e.location, config.places)
                located.append((e.title, e.start, e.end, targets[e.location]))
        leg = next(
            (g for g in service.plan_legs(located, home) if g.show_from <= now < g.show_until),
            None,
        )
        if leg is None:
            return HTMLResponse("")  # no trip right now: the card disappears

        options, leave_by = [], None
        if leg.origin and leg.dest:
            not_before = max(now, leg.not_before or now) + timedelta(
                minutes=leg.origin.walk_minutes
            )
            if leg.arrive_by:
                arrive_by = leg.arrive_by - timedelta(minutes=leg.dest.walk_minutes)
            else:
                arrive_by = not_before + HOME_WINDOW
            options = service.connections(
                session,
                leg.origin,
                leg.dest,
                arrive_by=arrive_by,
                not_before=not_before,
                tz=config.tz,
                latest_first=leg.arrive_by is not None,
            )
            if options and leg.arrive_by:
                leave_by = options[0].departs - timedelta(minutes=leg.origin.walk_minutes)
            if options:
                add_live_delays(session, options, now)

        end = service.timetable_end(session)
    return templates.TemplateResponse(
        request,
        "widget.html",
        {
            "timetable_end": end if end and end < now.astimezone(config.tz).date() else None,
            "leg": leg,
            "options": options,
            "leave_by": leave_by,
            "tz": config.tz,
            "stale": is_stale(engine, "mpk", timedelta(days=2)),
        },
    )
