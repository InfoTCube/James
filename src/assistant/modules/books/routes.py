"""Dashboard widget: this month's book suggestions. Hidden until there are any."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.db import get_engine, utcnow
from assistant.core.notifier import notify
from assistant.modules.books import service
from assistant.modules.books.models import Book

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/books")
def books_widget(request: Request):
    engine = get_engine()
    with Session(engine) as session:
        books = service.suggestions(session)
        if not books:
            return HTMLResponse("")
        stale = is_stale(engine, "books", timedelta(days=3))  # runs every night
        return templates.TemplateResponse(request, "widget.html", {"books": books, "stale": stale})


@router.post("/api/books/read/{book_id}")
def read_button(request: Request, book_id: str):
    """The card's "Read it" button: marks the book, then shows the card again without it."""
    with Session(get_engine()) as session, session.begin():
        book = session.get(Book, book_id)
        if book is not None:
            service.mark_read(session, book.title, utcnow())
    return books_widget(request)


@router.post("/api/books/send/{book_id}")
def send_to_telegram(book_id: str):
    """A book tapped on the dashboard: send it with its link to Telegram."""
    with Session(get_engine()) as session, session.begin():
        book = session.get(Book, book_id)
        if book is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, service.format_book(book))
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
