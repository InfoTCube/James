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


def devpost_days(s: str) -> tuple[date, date]:
    """ "Aug 31 - Oct 23, 2026", "Oct 06 - 12, 2026", "Dec 31, 2026", "Dec 15, 2026 - Jan 10,
    2027" → (first, last day)."""
    left, _, right = s.partition(" - ")
    right = right or left
    if right[0].isdigit():  # "12, 2026": same month as the start
        right = f"{left[:3]} {right}"
    end = datetime.strptime(right, "%b %d, %Y").date()
    start = datetime.strptime(left if "," in left else f"{left}, {end.year}", "%b %d, %Y").date()
    return (start.replace(year=start.year - 1) if start > end else start), end


def parse_devpost(data: dict) -> list[Event]:
    events = []
    for h in data["hackathons"]:
        place = h["displayed_location"]
        starts, ends = devpost_days(h["submission_period_dates"])
        online = place["icon"] == "globe" or "online" in fold(place["location"])
        events.append(
            Event(
                id=f"devpost:{h['id']}",
                source="devpost",
                title=h["title"].strip(),
                url=h["url"],
                kind="hackathon",
                topics=", ".join(t["name"] for t in h["themes"]),
                city="" if place["icon"] == "globe" else place["location"].strip(),
                online=online,
                starts=starts,
                ends=ends,
                at=None,  # dates only
                free=None,
            )
        )
    return events


def parse_mlh(html: str, tz: ZoneInfo) -> list[Event]:
    m = re.search(r'<script data-page="app" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("mlh: no page data (layout changed?)")
    events = []
    for e in json.loads(m[1])["props"]["upcomingEvents"]:
        events.append(
            Event(
                id=f"mlh:{e['slug']}",
                source="mlh",
                title=e["name"],
                url=e["websiteUrl"] or f"https://www.mlh.com{e['url']}",
                kind="hackathon",
                topics="",
                city=(e["location"] or "").strip(),
                online="physical" not in e["formatType"],  # "hybrid_physical" is in person
                starts=datetime.fromisoformat(e["startsAt"]).astimezone(tz).date(),
                ends=datetime.fromisoformat(e["endsAt"]).astimezone(tz).date(),
                at=None,  # startsAt times are placeholders ("01:11:11")
                free=None,
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


def fetch_devpost(cfg: EventsConfig, today: date, tz: ZoneInfo) -> list[Event]:
    events = []
    for page in range(1, 11):  # ~175 open + upcoming, 40 a page
        data = get(
            "https://devpost.com/api/hackathons",
            **{"status[]": ["upcoming", "open"], "per_page": 40, "page": page},
        ).json()
        events += parse_devpost(data)
        if len(data["hackathons"]) < 40:
            break
    return events


def fetch_mlh(cfg: EventsConfig, today: date, tz: ZoneInfo) -> list[Event]:
    season = today.year + (today.month >= 7)  # the 2027 season starts in summer 2026
    return parse_mlh(get(f"https://www.mlh.com/seasons/{season}/events").text, tz)


Fetch = Callable[[EventsConfig, date, ZoneInfo], list[Event]]
# order matters: of an event on several sites, the first one's entry is kept (Meetup has times)
SOURCES: dict[str, tuple[str, Fetch]] = {
    "meetup": ("Meetup", fetch_meetup),
    "crossweb": ("Crossweb", fetch_crossweb),
    "devpost": ("Devpost", fetch_devpost),
    "mlh": ("MLH", fetch_mlh),
}


def save_new(session: Session, events: list[Event], cfg: EventsConfig, today: date) -> list[Event]:
    """Store matching events not started yet and not seen before, on any site (by dedupe key);
    return them. Duplicates aren't stored: the first site's entry stands for the event.
    Already running when first seen (Devpost lists hackathons open for months) = too late."""
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
