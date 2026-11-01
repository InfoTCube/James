"""Dashboard widget: this week's film suggestions. Hidden until there are any."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.db import get_engine, utcnow
from assistant.core.notifier import notify
from assistant.modules.films import service
from assistant.modules.films.models import Film

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/films")
def films_widget(request: Request):
    engine = get_engine()
    with Session(engine) as session:
        films = service.suggestions(session)
        if not films:
            return HTMLResponse("")
        stale = is_stale(engine, "films", timedelta(days=3))  # runs every night
        return templates.TemplateResponse(request, "widget.html", {"films": films, "stale": stale})


@router.post("/api/films/watched/{film_id}")
def watched_button(request: Request, film_id: int):
    """The card's "Watched" button: marks the film, then shows the card again without it."""
    with Session(get_engine()) as session, session.begin():
        film = session.get(Film, film_id)
        if film is not None:
            service.mark_watched(session, film.title, utcnow())
    return films_widget(request)


@router.post("/api/films/send/{film_id}")
def send_to_telegram(film_id: int):
    """A film tapped on the dashboard: send it with its link to Telegram."""
    with Session(get_engine()) as session, session.begin():
        film = session.get(Film, film_id)
        if film is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, service.format_film(film))
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
