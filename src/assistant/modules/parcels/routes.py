"""Dashboard widget: tracked parcels. Hidden when there are none."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine
from assistant.modules.parcels import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/parcels")
def parcels_widget(request: Request):
    tz, engine = load_config().tz, get_engine()
    with Session(engine) as session:
        lines = [service.format_parcel(p, tz).split("\n") for p in service.parcels(session)]
    if not lines:
        return HTMLResponse("")
    stale = is_stale(engine, "parcels", timedelta(hours=12))  # nothing runs 23–7
    return templates.TemplateResponse(request, "widget.html", {"lines": lines, "stale": stale})
