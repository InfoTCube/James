"""Dashboard widget: flight deals under the watches' prices, cheapest first; without deals, the
watches and how to add one."""

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale
from assistant.core.db import get_engine
from assistant.core.notifier import notify
from assistant.modules.flights import service
from assistant.modules.flights.models import FlightFare

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent)


@router.get("/widgets/flights")
def flights_widget(request: Request):
    engine = get_engine()
    with Session(engine) as session:
        deals = service.deals(session)
        # no deals: show the watches and how close they are, so the card says what it waits for
        waiting = []
        for w in [] if deals else service.watches(session):
            cheapest = next(iter(service.fares(session, w.id)), None)
            if w.checked_at is None:
                status = "checking within 15 minutes"
            elif cheapest:
                status = (
                    f"nothing under the price; cheapest {cheapest.price:.0f} zł {cheapest.city}"
                )
            else:
                status = "no flights found"
            waiting.append((service.describe(w), status))
        rows = [
            (
                f"{f.watch_id}/{f.airline}/{f.destination}",
                f"{f.price:.0f} zł",
                f"{f.city} ({f.destination})",
                f"{service.days(f)} · {f.airline} · watch #{w.id}",
            )
            for w, f in deals
        ]
    # runs every 15 min; a failing airline only warns, so a long gap means the worker is down
    stale = is_stale(engine, "flights", timedelta(hours=1))
    return templates.TemplateResponse(
        request, "widget.html", {"rows": rows, "waiting": waiting, "stale": stale}
    )


@router.post("/api/flights/send/{watch_id}/{airline}/{destination}")
def send_to_telegram(watch_id: int, airline: str, destination: str):
    """A deal tapped on the dashboard: send it (with its booking link) to Telegram.
    Returns the status text shown on the tapped row."""
    with Session(get_engine()) as session, session.begin():
        fare = session.get(FlightFare, (watch_id, airline, destination))
        if fare is None:
            return HTMLResponse("✗ gone")
        sent = notify(session, f"🔗 {service.format_fare(fare)}")
    return HTMLResponse("✓ sent to Telegram" if sent else "✗ not sent")
