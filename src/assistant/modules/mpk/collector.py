"""Wrocław GTFS timetable (open data portal) → mpk_* tables."""

import csv
import io
import logging
import re
import time
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from itertools import batched

import httpx
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import service
from assistant.modules.mpk.models import (
    MpkFeed,
    MpkGeocode,
    MpkRoute,
    MpkServiceDate,
    MpkServiceWeekday,
    MpkStop,
    MpkStopTime,
    MpkTrip,
)

log = logging.getLogger(__name__)

CATALOGUE = "https://api.open-data.cui.wroclaw.pl/od2/6/"  # dataset: "Rozkład jazdy (GTFS)"
DOWNLOAD = "https://open-data.cui.wroclaw.pl/hdb/download/{}/"
CANDIDATES = 6  # newest file ids to consider; ids aren't in date order
HEADERS = {"User-Agent": "assistant/0.1 (personal home dashboard)"}
WEEKDAY_COLS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def effective_date(filename: str) -> date | None:
    """'OtwartyWroclaw_rozklad_jazdy_GTFS_26092026.zip' → 2026-09-26."""
    m = re.search(r"_(\d{2})(\d{2})(\d{4})\.zip", filename)
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def pick_current(files: dict[int, str], today: date) -> int | None:
    """File id of the newest timetable already in effect today."""
    dated = [(d, fid) for fid, name in files.items() if (d := effective_date(name)) and d <= today]
    return max(dated)[1] if dated else None


def _secs(hms: str) -> int:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict]:
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def _service_dates(zf: zipfile.ZipFile) -> Iterator[dict]:
    dates: set[tuple[str, date]] = set()
    for c in _rows(zf, "calendar.txt"):
        d = datetime.strptime(c["start_date"], "%Y%m%d").date()
        end = datetime.strptime(c["end_date"], "%Y%m%d").date()
        while d <= end:
            if c[WEEKDAY_COLS[d.weekday()]] == "1":
                dates.add((c["service_id"], d))
            d += timedelta(days=1)
    if "calendar_dates.txt" in zf.namelist():
        for c in _rows(zf, "calendar_dates.txt"):
            key = (c["service_id"], datetime.strptime(c["date"], "%Y%m%d").date())
            if c["exception_type"] == "1":
                dates.add(key)
            else:
                dates.discard(key)
    return ({"service_id": s, "day": d} for s, d in dates)


def import_feed(session: Session, zf: zipfile.ZipFile) -> None:
    """Replace all mpk tables with the contents of a GTFS zip."""
    conn = session.connection()
    tables = {
        MpkStop: (
            {"stop_id": r["stop_id"], "name": r["stop_name"],
             "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])}
            for r in _rows(zf, "stops.txt")
        ),
        MpkRoute: (
            {"route_id": r["route_id"], "short_name": r["route_short_name"],
             "route_type": int(r["route_type"])}
            for r in _rows(zf, "routes.txt")
        ),
        MpkTrip: (
            {"trip_id": r["trip_id"], "route_id": r["route_id"],
             "service_id": r["service_id"], "headsign": r["trip_headsign"]}
            for r in _rows(zf, "trips.txt")
        ),
        MpkStopTime: (
            {"trip_id": r["trip_id"], "seq": int(r["stop_sequence"]), "stop_id": r["stop_id"],
             "arr": _secs(r["arrival_time"]), "dep": _secs(r["departure_time"])}
            for r in _rows(zf, "stop_times.txt")
        ),
        MpkServiceDate: _service_dates(zf),
        MpkServiceWeekday: (
            {"service_id": c["service_id"], "weekday": i}
            for c in _rows(zf, "calendar.txt")
            for i, col in enumerate(WEEKDAY_COLS)
            if c[col] == "1"
        ),
    }  # fmt: skip
    for model, rows in tables.items():
        conn.execute(delete(model))
        for chunk in batched(rows, 20_000):
            conn.execute(insert(model), list(chunk))


class MpkCollector:
    name = "mpk"
    schedule = CronTrigger(hour=4, minute=10)  # new timetables appear every ~2 weeks

    def run(self, session: Session) -> None:
        today = utcnow().astimezone(load_config().tz).date()
        with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client:
            ids = client.get(CATALOGUE).raise_for_status().json()["pliki"]
            files = {}
            for fid in sorted(ids, reverse=True)[:CANDIDATES]:
                head = client.head(DOWNLOAD.format(fid)).raise_for_status()
                disp = head.headers.get("content-disposition", "")
                files[fid] = m[1] if (m := re.search(r'filename="([^"]+)"', disp)) else ""
            current = pick_current(files, today)
            if current is None:
                raise RuntimeError(f"no timetable in effect among files {files}")
            if session.scalar(select(MpkFeed.file_id)) == current:
                log.info("timetable %s already imported", files[current])
                return
            data = client.get(DOWNLOAD.format(current)).raise_for_status().content

        import_feed(session, zipfile.ZipFile(io.BytesIO(data)))
        session.execute(delete(MpkFeed))
        session.add(MpkFeed(file_id=current, name=files[current], imported_at=utcnow()))
        log.info("imported timetable %s", files[current])


NOMINATIM = "https://nominatim.openstreetmap.org/search"
WROCLAW_BOX = "16.80,51.21,17.18,51.04"  # lon/lat viewbox: only Wrocław results
LOOKUPS_PER_RUN = 5  # Nominatim policy: max 1 request/s, be gentle


def geocode(client: httpx.Client, query: str) -> tuple[float, float] | None:
    """Address → (lat, lon) inside Wrocław via OpenStreetMap Nominatim, or None."""
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "pl",
              "viewbox": WROCLAW_BOX, "bounded": 1}  # fmt: skip
    hits = client.get(NOMINATIM, params=params).raise_for_status().json()
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


class GeocodeCollector:
    """Looks up calendar locations that no text rule matches, so trips can use nearby stops.
    Sends only the location text to OpenStreetMap; each text is looked up once."""

    name = "mpk_geocode"
    schedule = IntervalTrigger(minutes=15)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        events = calendar.events_between(session, now, now + timedelta(days=7), config.tz)
        names = service.stop_names(session)
        todo = [
            loc
            for loc in dict.fromkeys(e.location for e in events if e.location and not e.all_day)
            if not service.resolve(loc, config.places, names) and not session.get(MpkGeocode, loc)
        ][:LOOKUPS_PER_RUN]
        with httpx.Client(headers=HEADERS, timeout=20) as client:
            for i, loc in enumerate(todo):
                if i:
                    time.sleep(1.1)
                lat, lon = geocode(client, loc) or (None, None)
                session.add(MpkGeocode(query=loc, lat=lat, lon=lon, looked_up_at=utcnow()))
                log.info("geocoded %r: %s", loc, "found" if lat else "not found")
