"""Dashboard widget: this week's dinner ideas. Hidden when there are none."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.meals import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/meals")
def meals_widget(request: Request):
    today = utcnow().astimezone(load_config().tz).date()
    with Session(get_engine()) as session:
        ideas = [(m.name, m.uses, m.how) for m in service.ideas(session, today)]
    if not ideas:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "widget.html", {"ideas": ideas})
