"""Dashboard widget: matching job offers from the last 7 days, best fit first (hidden when
there are none)."""

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
from assistant.modules.jobs import service
from assistant.modules.jobs.models import JobOffer

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/jobs")
def jobs_widget(request: Request):
    config, now, engine = load_config(), utcnow(), get_engine()
    tz, cfg = config.tz, config.jobs
    with Session(engine) as session:
        offers = service.recent(session, now, cfg)
        if not offers:
            return HTMLResponse("")
        today = now.astimezone(tz).date()
        rows = []
        for o in offers:
            day = o.first_seen.astimezone(tz).date()
            ago = (today - day).days
            when = "today" if ago == 0 else "yesterday" if ago == 1 else f"{day:%a %d.%m}"
            details = f"{o.company} · {service.details(o, max_cities=2)}"
            rows.append((o.id, when, o.title, details, service.best_fit(o, cfg)))
    # runs every 4 h from 08:20 to 20:20, so 12 h overnight is normal
    stale = is_stale(engine, "jobs", timedelta(hours=13))
    return templates.TemplateResponse(request, "widget.html", {"rows": rows, "stale": stale})


@router.post("/api/jobs/send/{offer_id:path}")
def send_to_telegram(offer_id: str):
    """A job tapped on the dashboard: send it (with its link) to Telegram, to open on the phone.
    Returns the status text shown on the tapped row."""
    with Session(get_engine()) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, f"🔗 {service.format_offer(offer, load_config().jobs)}")
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
