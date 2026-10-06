"""IT event sites → events_events (matches only) + one Telegram alert with the new ones."""

import html as htmllib
import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.core.config import EventsConfig, fold, load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.events import service
from assistant.modules.events.models import Event

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (personal home assistant; a few requests a day)"}
TELEGRAM_MAX = 4096  # characters per message


def text(fragment: str) -> str:
    """HTML fragment → plain text with single spaces."""
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def slug(city: str) -> str:
    """ "Wrocław" → "wroclaw", as the sites' URLs spell cities."""
    return re.sub(r"\W+", "-", fold(city)).strip("-")


# --- parsers: site response → Event rows (first_seen is set by the collector) ---

CROSSWEB_KINDS = {"spotkanie": "meetup", "konferencja": "conference", "szkolenie": "training",
                  "warsztaty": "workshop", "konkurs": "contest"}  # fmt: skip
CROSSWEB_ROW = re.compile(r'<a class="clearfix" href="(/wydarzenia/([^"/]+)/)">(.*?)</a>', re.S)
CROSSWEB_COL = re.compile(r'class="colTab (\w+)[^"]*">(.*?)</div>\s*(?=<div class="colTab|$)', re.S)


def crossweb_day(ddmm: str, today: date) -> date:
    """ "14.10" → the date; the list has no years, so a date long past is next year's."""
    d = date(today.year, int(ddmm[3:5]), int(ddmm[:2]))
    return d.replace(year=d.year + 1) if d < today - timedelta(days=60) else d


def parse_crossweb(html: str, today: date) -> list[Event]:
    events = []
    for href, event_id, row in CROSSWEB_ROW.findall(html):
        col = dict(CROSSWEB_COL.findall(row))
        title_html, _, topics_html = col["title"].partition('<div class="topics')
        days = [crossweb_day(d, today) for d in re.findall(r"\d\d\.\d\d", col["date"])]
        place = text(col["city"]).split()
        kind = text(col["type"])
        events.append(
            Event(
                id=f"crossweb:{event_id}",
                source="crossweb",
                title=text(title_html),
                url=f"https://crossweb.pl{href}",
                kind=CROSSWEB_KINDS.get(fold(kind), kind.lower()),
                topics=re.sub(r"\s*[•,]\s*", ", ", text(topics_html.partition(">")[2])),
                city=" ".join(w for w in place if w != "Online"),
                online="Online" in place,
                starts=days[0],
                ends=days[-1],
                at=None,  # the list has dates only
                free={"Bezpłatny": True, "Płatny": False}.get(text(col["cost"])),
            )
        )
    return events


def parse_meetup(html: str, tz: ZoneInfo) -> list[Event]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("meetup: no __NEXT_DATA__ in page (layout changed?)")
    state = json.loads(m[1])["props"]["pageProps"]["__APOLLO_STATE__"]
    events = []
    for key, e in state.items():
        if not key.startswith("Event:") or "title" not in e:  # stubs without details
            continue
        at = datetime.fromisoformat(e["dateTime"])
        online = e.get("eventType") == "ONLINE"
        group = state.get(e["group"].get("__ref", ""), {})
        events.append(
            Event(
                id=f"meetup:{e['id']}",
                source="meetup",
                title=e["title"],
                url=e["eventUrl"],
                kind="meetup",
                topics=group.get("name", ""),  # e.g. "Wrocław .NET User Group"
                city="" if online else (e.get("venue") or {}).get("city", ""),
                online=online,
                starts=at.astimezone(tz).date(),
                ends=at.astimezone(tz).date(),
                at=at,
                free=None,  # ponytail: feeSettings is null for free ones, unsure it's never missing
            )
        )
    return events


# --- fetchers ---


def get(url: str, **params) -> httpx.Response:
    resp = httpx.get(url, params=params, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp


def fetch_meetup(cfg: EventsConfig, today: date, tz: ZoneInfo) -> list[Event]:
    events = []
    for city in cfg.cities:  # categoryId 546: Technology. The page has the first ~15 only.
        url = "https://www.meetup.com/find/"
        resp = get(url, location=f"pl--{slug(city)}", source="EVENTS", categoryId=546)
        events += parse_meetup(resp.text, tz)
    return events


def fetch_crossweb(cfg: EventsConfig, today: date, tz: ZoneInfo) -> list[Event]:
    pages = [slug(c) for c in cfg.cities] + ["online"] * cfg.online
    return [
        e
        for p in pages
        for e in parse_crossweb(get(f"https://crossweb.pl/wydarzenia/{p}/").text, today)
    ]


Fetch = Callable[[EventsConfig, date, ZoneInfo], list[Event]]
# order matters: of an event on several sites, the first one's entry is kept (Meetup has times)
SOURCES: dict[str, tuple[str, Fetch]] = {
    "meetup": ("Meetup", fetch_meetup),
    "crossweb": ("Crossweb", fetch_crossweb),
}


def save_new(session: Session, events: list[Event], cfg: EventsConfig, today: date) -> list[Event]:
    """Store matching events not started yet and not seen before, on any site (by dedupe key);
    return them. Duplicates aren't stored: the first site's entry stands for the event.
    Already running when first seen = too late."""
    now, new, seen = utcnow(), [], set()
    for e in events:
        if e.starts < today or not service.matches(e, cfg):
            continue
        e.dedupe_key = service.dedupe_key(e)
        if e.dedupe_key in seen or session.scalar(
            select(Event.id).where(Event.dedupe_key == e.dedupe_key).limit(1)
        ):
            continue
        seen.add(e.dedupe_key)
        e.first_seen = now
        session.add(e)
        new.append(e)
    return new


class EventsCollector:
    """All sites in one run, so an event listed on several sites is alerted once."""

    name = "events"
    schedule = CronTrigger(hour=9, minute=40)  # events are announced weeks ahead: daily is plenty

    def run(self, session: Session) -> None:
        config = load_config()
        cfg, tz = config.events, config.tz
        if not cfg.cities and not cfg.online:
            return
        today = utcnow().astimezone(tz).date()
        events, failed = [], []
        for source, (label, fetch) in SOURCES.items():  # before any write: no DB lock during HTTP
            try:
                events += fetch(cfg, today, tz)
            except Exception as e:  # one broken site mustn't hide the others' events
                log.exception("events: %s failed", label)
                failed.append((source, label, f"{type(e).__name__}: {e}"))
        if len(failed) == len(SOURCES):
            raise RuntimeError("all event sites failed")
        for source, label, error in failed:  # once a day per site
            notify(
                session,
                f"⚠️ Events: {label} failed: {error}"[:500],
                key=f"events-fail:{source}:{today}",
            )
        # over for a week: no site lists it any more, so it can't alert again
        session.execute(delete(Event).where(Event.ends < today - timedelta(days=7)))
        new = save_new(session, events, cfg, today)
        if new:
            notify(session, alert_text(new, tz))


def alert_text(new: list[Event], tz: ZoneInfo) -> str:
    """One message with as many events as fit Telegram's 4096 characters, soonest first."""
    new = sorted(new, key=lambda e: e.starts)
    parts = [f"📅 {len(new)} new event{'s' * (len(new) > 1)}"]
    for i, e in enumerate(new):
        more = f"…and {len(new) - i} more in /events"
        if len("\n\n".join([*parts, service.format_event(e, tz), more])) > TELEGRAM_MAX:
            parts.append(more)
            break
        parts.append(service.format_event(e, tz))
    return "\n\n".join(parts)
