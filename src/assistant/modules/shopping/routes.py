"""Dashboard widget: the shopping list, items on promotion flagged. Hidden when it's empty."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.shopping import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/shopping")
def shopping_widget(request: Request):
    today = utcnow().astimezone(load_config().tz).date()
    with Session(get_engine()) as session:
        lines = service.format_list(session, today)
    if not lines:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "widget.html", {"lines": lines})
