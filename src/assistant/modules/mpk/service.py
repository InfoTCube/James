"""Place matching and departures from the imported timetable. No network here."""

import difflib
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from assistant.core.config import Place, fold
from assistant.modules.mpk.models import MpkStop

DEFAULT_WALK = 5  # minutes, for stops matched by name rather than a configured place


@dataclass
class Target:
    label: str  # place alias or stop name
    stops: list[str]  # stop names
    walk_minutes: int


@dataclass
class Connection:
    line: str
    is_tram: bool
    headsign: str
    from_stop: str
    to_stop: str
    departs: datetime
    arrives: datetime


def resolve(location: str, places: dict[str, Place], stop_names: list[str]) -> Target | None:
    """Map free text (calendar location, later chat/voice) to stops.

    1. a place alias or one of its keywords appears in the text ("uni", "Politechnika, C-13")
    2. a stop name appears in the text ("Pl. Grunwaldzki 1" → PL. GRUNWALDZKI)
    3. fuzzy match against all stop names ("Grunwaldzky" → PL. GRUNWALDZKI)
    A matched stop that belongs to a place resolves to that place.
    """
    loc = re.sub(r"\bplac\b", "pl.", fold(location))  # timetable writes "PL. GRUNWALDZKI"
    words = set(re.findall(r"\w+", loc))
    for alias, place in places.items():
        if alias in words or any(fold(k) in loc for k in place.keywords):
            return Target(alias, place.stops, place.walk_minutes)

    by_fold: dict[str, list[str]] = {}  # "dworzec glowny" → tram + bus spellings
    for name in stop_names:
        by_fold.setdefault(fold(name), []).append(name)
    inside = [f for f in by_fold if len(f) > 3 and re.search(rf"\b{re.escape(f)}\b", loc)]
    close = difflib.get_close_matches(loc, list(by_fold), n=1, cutoff=0.8)
    match = max(inside, key=len) if inside else (close[0] if close else None)
    if match is None:
        return None
    names = by_fold[match]
    for alias, place in places.items():
        if set(names) & set(place.stops):
            return Target(alias, place.stops, place.walk_minutes)
    return Target(names[0], names, DEFAULT_WALK)


def stop_names(session: Session) -> list[str]:
    return list(session.scalars(select(MpkStop.name).distinct()))


def _names_sql(prefix: str, names: list[str]) -> tuple[str, dict]:
    keys = {f"{prefix}{i}": n for i, n in enumerate(names)}
    return ", ".join(f":{k}" for k in keys), keys


def connections(
    session: Session,
    origin: Target,
    dest: Target,
    arrive_by: datetime,
    not_before: datetime,
    tz: ZoneInfo,
    limit: int = 3,
) -> list[Connection]:
    """Direct trips from origin to dest that leave the origin stop at/after `not_before`
    and reach the dest stop by `arrive_by`. Latest arrivals first, one row per trip.

    ponytail: direct trips only, same service day only (no after-midnight trips of the
    previous day). Add transfers (e.g. RAPTOR) if a common trip needs a change.
    """
    day = arrive_by.astimezone(tz).date()
    midnight = datetime.combine(day, time.min, tz)
    o_sql, o_keys = _names_sql("o", origin.stops)
    d_sql, d_keys = _names_sql("d", dest.stops)
    rows = session.execute(
        text(f"""
            SELECT a.trip_id, a.dep, b.arr, r.short_name, r.route_type, t.headsign,
                   sa.name AS from_stop, sb.name AS to_stop
            FROM mpk_stop_times a
            JOIN mpk_stops sa ON sa.stop_id = a.stop_id AND sa.name IN ({o_sql})
            JOIN mpk_stop_times b ON b.trip_id = a.trip_id AND b.seq > a.seq
            JOIN mpk_stops sb ON sb.stop_id = b.stop_id AND sb.name IN ({d_sql})
            JOIN mpk_trips t ON t.trip_id = a.trip_id
            JOIN mpk_service_dates sd ON sd.service_id = t.service_id AND sd.day = :day
            JOIN mpk_routes r ON r.route_id = t.route_id
            WHERE a.dep >= :earliest AND b.arr <= :latest
        """),  # noqa: S608  names are bound parameters, only placeholders are formatted
        {
            **o_keys,
            **d_keys,
            "day": day.isoformat(),
            "earliest": int((not_before - midnight).total_seconds()),
            "latest": int((arrive_by - midnight).total_seconds()),
        },
    ).all()

    best: dict[str, tuple] = {}  # per trip: board as late as possible, get off as early
    for r in rows:
        cur = best.get(r.trip_id)
        if cur is None or (r.dep, -r.arr) > (cur.dep, -cur.arr):
            best[r.trip_id] = r
    ordered = sorted(best.values(), key=lambda r: (r.arr, r.dep), reverse=True)[:limit]
    return [
        Connection(
            line=r.short_name,
            is_tram=r.route_type == 0,
            headsign=r.headsign,
            from_stop=r.from_stop,
            to_stop=r.to_stop,
            departs=midnight + timedelta(seconds=r.dep),
            arrives=midnight + timedelta(seconds=r.arr),
        )
        for r in ordered
    ]
