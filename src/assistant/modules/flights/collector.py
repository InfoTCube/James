"""Ryanair + Wizz Air fares per watch → flights_fares, and one Telegram alert with new deals."""

import logging
import re
import time
from collections.abc import Callable
from datetime import date, timedelta

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.flights import service
from assistant.modules.flights.models import FlightFare, FlightWatch

log = logging.getLogger(__name__)

ORIGIN = "WRO"
HEADERS = {"User-Agent": "Mozilla/5.0 (personal home assistant; a few requests a day)"}
EVERY = timedelta(hours=12)  # each watch is checked twice a day
DAY = (7, 22)  # local hours when watches are checked (and alerts sent)
DROP = 0.9  # a deal already sent is sent again when it gets 10% cheaper
TELEGRAM_MAX = 4096
WIZZ_MAX_DAYS = 42  # the timetable refuses longer ranges


def first_out(w: FlightWatch, today: date) -> date:
    return max(w.first_day, today)


def last_out(w: FlightWatch) -> date:
    return w.last_day - timedelta(days=w.nights_min or 0)


# --- Ryanair: one request per watch gives the cheapest trip to every destination ---


def ryanair_url(dest: str, out: date, back: date | None) -> str:
    return (
        "https://www.ryanair.com/gb/en/trip/flights/select?adults=1"
        f"&dateOut={out}&dateIn={back or ''}&originIata={ORIGIN}&destinationIata={dest}"
        f"&isReturn={'true' if back else 'false'}"
    )


def parse_ryanair(data: dict) -> list[FlightFare]:
    fares = []
    for f in data["fares"]:
        airport = f["outbound"]["arrivalAirport"]
        out = date.fromisoformat(f["outbound"]["departureDate"][:10])
        back = f.get("inbound") and date.fromisoformat(f["inbound"]["departureDate"][:10])
        fares.append(
            FlightFare(
                airline="Ryanair",
                destination=airport["iataCode"],
                city=airport["name"],
                out_day=out,
                back_day=back,
                price=f["summary"]["price"]["value"],
                url=ryanair_url(airport["iataCode"], out, back),
            )
        )
    return fares


def fetch_ryanair(watches: list[FlightWatch], today: date) -> dict[int, list[FlightFare]]:
    found = {}
    for w in watches:
        params = {
            "departureAirportIataCode": ORIGIN,
            "outboundDepartureDateFrom": first_out(w, today),
            "outboundDepartureDateTo": last_out(w),
            "market": "en-gb",
            "currency": "PLN",
        }
        if w.nights_min is None:
            url = "https://www.ryanair.com/api/farfnd/v4/oneWayFares"
        else:
            url = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"
            params |= {
                "inboundDepartureDateFrom": first_out(w, today),
                "inboundDepartureDateTo": w.last_day,
                "durationFrom": w.nights_min,
                "durationTo": w.nights_max,
            }
        resp = httpx.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        found[w.id] = parse_ryanair(resp.json())
    return found


# --- Wizz Air: daily one-way prices per route; the cheapest trip is put together here ---


def wizz_api(html: str) -> str:
    """The API's base URL changes with each Wizz release; it's in the home page."""
    m = re.search(r'apiUrl:"(https://be\.wizzair\.com/[^"]+)"', html)
    if not m:
        raise ValueError("wizz: no apiUrl in the home page (layout changed?)")
    return m[1]


def wizz_routes(data: dict) -> dict[str, str]:
    """Direct destinations from ORIGIN: IATA code → city name."""
    names = {c["iata"]: c["shortName"] for c in data["cities"]}
    origin = next(c for c in data["cities"] if c["iata"] == ORIGIN)
    return {c["iata"]: names.get(c["iata"], c["iata"]) for c in origin["connections"]
            if c["isDirectFlight"]}  # fmt: skip


def wizz_prices(flights: list[dict] | None) -> dict[date, float]:
    """Timetable → price per day. Days without a price ("checkPrice") are left out."""
    return {
        date.fromisoformat(f["departureDate"][:10]): f["price"]["amount"]
        for f in flights or []
        if f["priceType"] == "price" and f["price"]["amount"] > 0
    }


def wizz_url(dest: str, out: date, back: date | None) -> str:
    return (
        f"https://www.wizzair.com/en-gb/booking/select-flight/{ORIGIN}/{dest}/{out}/"
        f"{back or 'null'}/1/0/0/null"
    )


def wizz_best(
    dest: str, city: str, out: dict[date, float], back: dict[date, float], w: FlightWatch,
    today: date,
) -> FlightFare | None:  # fmt: skip
    """The cheapest trip for the watch from daily prices, or None."""
    days = [d for d in out if first_out(w, today) <= d <= last_out(w)]
    if w.nights_min is None:
        trips = [(out[d], d, None) for d in days]
    else:
        trips = [
            (out[d] + back[r], d, r)
            for d in days
            for n in range(w.nights_min, w.nights_max + 1)
            if (r := d + timedelta(days=n)) in back and r <= w.last_day
        ]
    if not trips:
        return None
    price, d, r = min(trips, key=lambda t: t[0])
    return FlightFare(airline="Wizz Air", destination=dest, city=city, out_day=d, back_day=r,
                      price=price, url=wizz_url(dest, d, r))  # fmt: skip


def fetch_wizz(watches: list[FlightWatch], today: date) -> dict[int, list[FlightFare]]:
    get = lambda url: httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)  # noqa: E731
    home = get("https://www.wizzair.com/en-gb")
    home.raise_for_status()
    api = wizz_api(home.text)
    resp = get(f"{api}/asset/map?languageCode=en-gb")
    resp.raise_for_status()
    routes = wizz_routes(resp.json())
    cache: dict[tuple, dict[date, float]] = {}  # watches often share routes and days

    def prices(frm: str, to: str, first: date, last: date) -> dict[date, float]:
        if (frm, to, first, last) not in cache:
            found, start = {}, first
            while start <= last:  # in pieces the timetable accepts
                end = min(last, start + timedelta(days=WIZZ_MAX_DAYS - 1))
                time.sleep(1)  # ponytail: ~30 routes in a row, keep it slow
                r = httpx.post(
                    f"{api}/search/timetable",
                    json={
                        "flightList": [
                            {
                                "departureStation": frm,
                                "arrivalStation": to,
                                "from": str(start),
                                "to": str(end),
                            }
                        ],
                        "priceType": "regular",
                        "adultCount": 1,
                        "childCount": 0,
                        "infantCount": 0,
                    },  # fmt: skip
                    headers=HEADERS,
                    timeout=30,
                )
                r.raise_for_status()
                found |= wizz_prices(r.json().get("outboundFlights"))
                start = end + timedelta(days=1)
            cache[frm, to, first, last] = found
        return cache[frm, to, first, last]

    found = {}
    for w in watches:
        wanted = w.destinations.split(",") if w.destinations else list(routes)
        found[w.id] = []
        for dest in (d for d in wanted if d in routes):
            out = prices(ORIGIN, dest, first_out(w, today), last_out(w))
            back = {}
            if w.nights_min is not None and out:
                back = prices(dest, ORIGIN, first_out(w, today) + timedelta(days=w.nights_min),
                              w.last_day)  # fmt: skip
            fare = wizz_best(dest, routes[dest], out, back, w, today)
            if fare:
                found[w.id].append(fare)
    return found


Fetch = Callable[[list[FlightWatch], date], dict[int, list[FlightFare]]]
SOURCES: dict[str, Fetch] = {"Ryanair": fetch_ryanair, "Wizz Air": fetch_wizz}


def update_fares(
    session: Session, w: FlightWatch, airline: str, found: list[FlightFare]
) -> list[FlightFare]:
    """Replace the watch's fares from `airline` with `found`; return the new deals: under the
    max price and not sent before, or 10% cheaper than when sent."""
    if w.destinations:
        found = [f for f in found if f.destination in w.destinations.split(",")]
    old = {
        f.destination: f
        for f in session.scalars(
            select(FlightFare).where(FlightFare.watch_id == w.id, FlightFare.airline == airline)
        )
    }
    deals = []
    for f in found:
        row = old.pop(f.destination, None)
        if row is None:
            f.watch_id = w.id
            session.add(row := f)
        else:
            row.city, row.out_day, row.back_day = f.city, f.out_day, f.back_day
            row.price, row.url = f.price, f.url
        if row.price <= w.max_price and (
            row.alerted_price is None or row.price <= row.alerted_price * DROP
        ):
            row.alerted_price = row.price
            deals.append(row)
    for gone in old.values():  # sold out or no longer flown in those days
        session.delete(gone)
    return deals


class FlightsCollector:
    """Checks each watch every 12 h; a new or repriced watch (checked_at None) within 15 min.
    Only in the daytime (DAY), so alerts don't wake you: a check due at night waits."""

    name = "flights"
    schedule = IntervalTrigger(minutes=15)

    def run(self, session: Session) -> None:
        tz, now = load_config().tz, utcnow()
        local = now.astimezone(tz)
        if not DAY[0] <= local.hour < DAY[1]:  # also guards the run at worker start
            return
        today = local.date()
        watches = service.watches(session)
        over = [w for w in watches if last_out(w) < today]
        due = [
            w for w in watches
            if w not in over and (w.checked_at is None or now - w.checked_at >= EVERY)
        ]  # fmt: skip
        found, failed = {}, []
        # all HTTP before any write: no DB lock held for the minute Wizz Air takes
        for airline, fetch in SOURCES.items() if due else ():
            try:
                found[airline] = fetch(due, today)
            except Exception as e:  # one broken airline mustn't hide the other's deals
                log.exception("flights: %s failed", airline)
                failed.append((airline, f"{type(e).__name__}: {e}"))
        if failed and len(failed) == len(SOURCES):
            raise RuntimeError("all airlines failed")
        for w in over:
            notify(session, f"✈️ Watch over, removed: {service.describe(w)}")
            service.delete_watch(session, w.id)
        for airline, error in failed:  # once a day per airline; its old fares are kept
            notify(session, f"⚠️ Flights: {airline} failed: {error}"[:500],
                   key=f"flights-fail:{airline}:{today}")  # fmt: skip
        deals = []
        for w in due:
            w.checked_at = now
            for airline, by_watch in found.items():
                deals += [(w, f) for f in update_fares(session, w, airline, by_watch[w.id])]
        if deals:
            notify(session, alert_text(deals))


def alert_text(deals: list[tuple[FlightWatch, FlightFare]]) -> str:
    """One message, cheapest first, as many as fit in Telegram's 4096 characters."""
    deals = sorted(deals, key=lambda d: d[1].price)
    parts = [f"✈️ {len(deals)} flight deal{'s' * (len(deals) > 1)}"]
    for i, (w, f) in enumerate(deals):
        line = f"{service.format_fare(f)}\n(watch #{w.id}, ≤ {w.max_price} zł)"
        more = f"…and {len(deals) - i} more in /flights"
        if len("\n\n".join([*parts, line, more])) > TELEGRAM_MAX:
            parts.append(more)
            break
        parts.append(line)
    return "\n\n".join(parts)
