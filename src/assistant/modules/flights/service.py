"""Flight watches (set from Telegram) and the fares found for them. No network here."""

import re
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.modules.flights.models import FlightFare, FlightWatch

USAGE = (
    "Usage: /flight_add <where> <dates> <nights> <max price>\n"
    "  /flight_add any 1.11-30.11 2-4 300\n"
    "  /flight_add BCN,MAD 15.12-6.01 5-9 600\n"
    "  /flight_add LTN 20.11 oneway 150\n"
    "where: airport codes or 'any' · dates: the whole trip within them · nights: 3, 2-4 or "
    "'oneway' · price: PLN, there and back together"
)


def parse_day(text: str, today: date, after: date | None = None) -> date:
    """ "1.11" or "1.11.2026" → a date; without a year the next one not before `after` (or
    today)."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", text)
    if not m:
        raise ValueError(text)
    day, month = int(m[1]), int(m[2])
    if m[3]:
        return date(int(m[3]), month, day)
    d = date(today.year, month, day)
    return d if d >= (after or today) else d.replace(year=d.year + 1)


def parse_watch(args: list[str], today: date) -> FlightWatch:
    """/flight_add arguments → an unsaved watch. Raises ValueError with what's wrong."""
    if len(args) != 4:
        raise ValueError("Four things needed.")
    where, dates, nights, price = args
    codes = [] if where.lower() == "any" else where.upper().split(",")
    if not all(re.fullmatch(r"[A-Z]{3}", c) for c in codes):
        raise ValueError(f"'{where}': airport codes like BCN or BCN,MAD, or 'any'.")
    try:
        first_text, _, last_text = dates.partition("-")
        first = parse_day(first_text, today)
        last = parse_day(last_text, today, after=first) if last_text else first
    except ValueError:
        raise ValueError(f"'{dates}': dates like 1.11-30.11 or 20.11.") from None
    if nights.lower() in ("oneway", "1way", "ow"):
        nights_min = nights_max = None
    elif m := re.fullmatch(r"(\d+)(?:-(\d+))?", nights):
        nights_min, nights_max = int(m[1]), int(m[2] or m[1])
        if nights_min > nights_max or (last - first).days < nights_min:
            raise ValueError(f"'{nights}' nights don't fit in {dates}.")
    else:
        raise ValueError(f"'{nights}': nights like 3 or 2-4, or 'oneway'.")
    return FlightWatch(
        destinations=",".join(codes),
        first_day=first,
        last_day=last,
        nights_min=nights_min,
        nights_max=nights_max,
        max_price=parse_price(price),
    )


def parse_price(text: str) -> int:
    """ "300", "300zł", "300pln" → 300."""
    m = re.fullmatch(r"(\d+)\s*(zł|zl|pln)?", text.lower())
    if not m:
        raise ValueError(f"'{text}': a price in PLN, like 300.")
    return int(m[1])


def describe(w: FlightWatch) -> str:
    """ "#3 anywhere · 1.11–30.11 · 2–4 nights · ≤ 300 zł"."""
    where = w.destinations.replace(",", ", ") or "anywhere"
    days = f"{w.first_day:%d.%m}" + (f"–{w.last_day:%d.%m}" if w.last_day != w.first_day else "")
    if w.nights_min is None:
        nights = "one way"
    elif w.nights_min == w.nights_max:
        nights = f"{w.nights_min} night{'s' * (w.nights_min != 1)}"
    else:
        nights = f"{w.nights_min}–{w.nights_max} nights"
    return f"#{w.id} {where} · {days} · {nights} · ≤ {w.max_price} zł"


def watches(session: Session) -> list[FlightWatch]:
    return list(session.scalars(select(FlightWatch).order_by(FlightWatch.id)))


def get_watch(session: Session, watch_id: int) -> FlightWatch | None:
    return session.get(FlightWatch, watch_id)


def fares(session: Session, watch_id: int) -> list[FlightFare]:
    """Cheapest trip per airline and destination at the last check, cheapest first."""
    return list(
        session.scalars(
            select(FlightFare).where(FlightFare.watch_id == watch_id).order_by(FlightFare.price)
        )
    )


def delete_watch(session: Session, watch_id: int) -> FlightWatch | None:
    """Delete a watch and its fares; None if there's no such watch."""
    w = session.get(FlightWatch, watch_id)
    if w:
        session.execute(delete(FlightFare).where(FlightFare.watch_id == watch_id))
        session.delete(w)
        session.flush()  # gone for the rest of the transaction too
    return w


def days(f: FlightFare) -> str:
    """ "Mon 09.11 → Fri 13.11", or just the day out for one way."""
    return f"{f.out_day:%a %d.%m}" + (f" → {f.back_day:%a %d.%m}" if f.back_day else "")


def format_fare(f: FlightFare) -> str:
    """Two lines: "131 zł Gdańsk (GDN) · Ryanair · Mon 09.11 → Fri 13.11", link."""
    return f"{f.price:.0f} zł {f.city} ({f.destination}) · {f.airline} · {days(f)}\n{f.url}"


def deals(session: Session) -> list[tuple[FlightWatch, FlightFare]]:
    """Fares under their watch's max price, cheapest first."""
    rows = session.execute(
        select(FlightWatch, FlightFare)
        .join(FlightFare, FlightFare.watch_id == FlightWatch.id)
        .where(FlightFare.price <= FlightWatch.max_price)
        .order_by(FlightFare.price)
    )
    return [tuple(r) for r in rows]
