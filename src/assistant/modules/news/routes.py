"""Dashboard widget: today's digest (made overnight) and the latest headlines; just the headlines
without a digest. Hidden when news is off or nothing has been fetched."""

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
from assistant.modules.news import service
from assistant.modules.news.models import NewsItem

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/news")
def news_widget(request: Request):
    tz, now, engine = load_config().tz, utcnow(), get_engine()
    with Session(engine) as session:
        d = service.digest(session, now.astimezone(tz).date())
        points = service.bullets(d) if d else []
        rows = [(i.id, f"{i.published.astimezone(tz):%H:%M}", i.title, i.feed)
                for i in service.latest(session, now)]  # fmt: skip
    if not points and not rows:
        return HTMLResponse("")
    stale = is_stale(engine, "news", timedelta(hours=3))  # fetched hourly
    return templates.TemplateResponse(
        request, "widget.html", {"points": points, "rows": rows, "stale": stale}
    )


@router.post("/api/news/send/{item_id}")
def send_to_telegram(item_id: str):
    """A headline tapped on the dashboard: send it with its link to Telegram."""
    with Session(get_engine()) as session, session.begin():
        item = session.get(NewsItem, item_id)
        if item is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, f"🔗 {item.title} ({item.feed})\n{item.link}")
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
