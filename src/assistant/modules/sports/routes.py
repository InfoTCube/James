"""Dashboard widget: next matches and recent scores, your teams in bold. Hidden when there are
none."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.sports import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/sports")
def sports_widget(request: Request):
    config, now, engine = load_config(), utcnow(), get_engine()
    with Session(engine) as session:
        names = service.followed_names(session, config.sports.teams)

        def rows(matches):
            return [
                (service.format_match(m, config.tz), service.is_followed(m, names)) for m in matches
            ]

        upcoming = rows(service.upcoming(session, now))
        results = rows(service.results(session, now))
    if not upcoming and not results:
        return HTMLResponse("")
    stale = is_stale(engine, "sports", timedelta(days=1))  # runs every 3 h
    return templates.TemplateResponse(
        request, "widget.html", {"upcoming": upcoming, "results": results, "stale": stale}
    )
