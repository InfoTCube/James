"""Dashboard widget: matching events in the next 30 days, soonest first (hidden when there are
none)."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.core.notifier import notify
from assistant.modules.events import service
from assistant.modules.events.models import Event

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/events")
def events_widget(request: Request):
    tz, engine = load_config().tz, get_engine()
    with Session(engine) as session:
        events = service.upcoming(session, utcnow().astimezone(tz).date())
        if not events:
            return HTMLResponse("")
        rows = [(e.id, service.when(e, tz), e.title, service.details(e)) for e in events]
    stale = is_stale(engine, "events", timedelta(hours=26))  # runs daily
    return templates.TemplateResponse(request, "widget.html", {"rows": rows, "stale": stale})


@router.post("/api/events/send/{event_id:path}")
def send_to_telegram(event_id: str):
    """An event tapped on the dashboard: send it (with its link) to Telegram, to open on the
    phone. Returns the status text shown on the tapped row."""
    with Session(get_engine()) as session, session.begin():
        event = session.get(Event, event_id)
        if event is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, f"🔗 {service.format_event(event, load_config().tz)}")
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
