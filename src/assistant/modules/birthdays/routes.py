"""Dashboard widget: birthdays in the next 7 days (hidden when there are none)."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.birthdays import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/birthdays")
def birthdays_widget(request: Request):
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        items = service.upcoming(session, now, config.tz)
    if not items:
        return HTMLResponse("")
    today = now.astimezone(config.tz).date()
    rows = [(service.when(b.day, today), b.name) for b in items]
    return templates.TemplateResponse(request, "widget.html", {"rows": rows})
