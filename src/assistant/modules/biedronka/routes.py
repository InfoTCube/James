"""Dashboard widget: your products on promotion. Hidden when there are none."""

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
from assistant.modules.biedronka import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


def _today():
    return utcnow().astimezone(load_config().tz).date()


@router.get("/widgets/biedronka")
def biedronka_widget(request: Request):
    engine = get_engine()
    with Session(engine) as session:
        offers = service.offers(session, _today())
    if not offers:
        return HTMLResponse("")
    stale = is_stale(engine, "biedronka", timedelta(days=3))  # runs every night
    return templates.TemplateResponse(request, "widget.html", {"offers": offers, "stale": stale})


@router.post("/api/biedronka/send/{page_id}")
def send_to_telegram(page_id: int):
    """An offer tapped on the dashboard: send that page's offers, with its image, to Telegram."""
    with Session(get_engine()) as session, session.begin():
        found = [o for o in service.offers(session, _today()) if o.page_id == page_id]
        if not found:
            return HTMLResponse("✗ gone")
        sent = notify(session, "\n\n".join(service.format_offer(o) for o in found))
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
