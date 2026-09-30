"""Dashboard widget: current weather, next hours, clothing advice."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.calendar import service as calendar
from assistant.modules.weather import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/weather")
def weather_widget(request: Request):
    tz, now, engine = load_config().tz, utcnow(), get_engine()
    with Session(engine) as session:
        hours = service.get_hours(session, now - timedelta(hours=1), now + timedelta(hours=24))
        events = calendar.outdoor_events(session, now, tz)
        advice = service.get_clothing_advice(session, now, tz, events)
    return templates.TemplateResponse(
        request,
        "widget.html",
        {
            "now": hours[0] if hours else None,
            "strip": hours[3::3][:6],  # every 3h, next ~18h
            "advice": advice,
            "stale": is_stale(engine, "weather", timedelta(hours=3)),
            "tz": tz,
            "describe": service.describe,
        },
    )
