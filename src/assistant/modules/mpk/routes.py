"""Dashboard widget: how to get to the next event."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)

LOOKAHEAD = timedelta(hours=2)  # show the trip only this long before the event starts


@router.get("/widgets/mpk")
def mpk_widget(request: Request):
    config, now, engine = load_config(), utcnow(), get_engine()
    tz, ctx = config.tz, {"event": None, "target": None, "options": [], "leave_by": None}
    with Session(engine) as session:
        event = calendar.next_event_with_location(session, now, tz)
        if event and event.start - now <= LOOKAHEAD and "home" in config.places:
            home = config.places["home"]
            origin = service.Target("home", home.stops, home.walk_minutes)
            target = service.resolve(event.location, config.places, service.stop_names(session))
            ctx |= {"event": event, "target": target}
            if target and target.label != "home":
                ctx["options"] = service.connections(
                    session,
                    origin,
                    target,
                    arrive_by=event.start - timedelta(minutes=target.walk_minutes),
                    not_before=now + timedelta(minutes=origin.walk_minutes),
                    tz=tz,
                )
                if ctx["options"]:
                    first = ctx["options"][0].departs
                    ctx["leave_by"] = first - timedelta(minutes=origin.walk_minutes)
    return templates.TemplateResponse(
        request,
        "widget.html",
        {**ctx, "tz": tz, "stale": is_stale(engine, "mpk", timedelta(days=2))},
    )
