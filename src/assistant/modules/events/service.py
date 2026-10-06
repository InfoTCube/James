"""Which events match the config, and reading stored matches. No network here."""

import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import EventsConfig, fold, has_word
from assistant.modules.events.models import Event


def in_cities(event: Event, cfg: EventsConfig) -> bool:
    return any(fold(c) in fold(event.city) for c in cfg.cities)


def matches(event: Event, cfg: EventsConfig) -> bool:
    """True if the event is worth an alert under `cfg`: in one of `cities`, or online with one
    of `keywords` (but no online hackathons); never with an `exclude` word (see the events
    section in config.yaml)."""
    about = f"{event.title} {event.kind} {event.topics}"
    if any(has_word(about, x) for x in cfg.exclude):
        return False
    if in_cities(event, cfg):
        return True
    return (
        cfg.online
        and event.online
        and event.kind != "hackathon"  # the user doesn't want online-only ones
        and any(has_word(about, k) for k in cfg.keywords)
    )


def dedupe_key(event: Event) -> str:
    """Same event on several sites (Crossweb lists most Meetup events) → same key: start day +
    title words in any order, case and diacritics ignored."""
    # ponytail: exact title words; a reworded title on the other site is alerted twice
    return f"{event.starts}|{' '.join(sorted(set(re.findall(r'\w+', fold(event.title)))))}"


def upcoming(session: Session, today: date, days: int = 30) -> list[Event]:
    """Stored matches not over yet and starting within `days` days, soonest first."""
    return list(
        session.scalars(
            select(Event)
            .where(Event.ends >= today, Event.starts <= today + timedelta(days=days))
            .order_by(Event.starts, Event.at)
        )
    )


def when(e: Event, tz: ZoneInfo) -> str:
    """ "Wed 14.10 18:00", or "Tue 20.10–Wed 21.10" for several days."""
    if e.ends != e.starts:
        return f"{e.starts:%a %d.%m}–{e.ends:%a %d.%m}"
    return f"{e.starts:%a %d.%m}" + (f" {e.at.astimezone(tz):%H:%M}" if e.at else "")


def details(e: Event) -> str:
    """Where · kind · free/paid, whatever is known."""
    where = ", ".join(filter(None, [e.city, "online" if e.online else None]))
    return " · ".join(filter(None, [where, e.kind, {True: "free", False: "paid"}.get(e.free)]))


def format_event(e: Event, tz: ZoneInfo) -> str:
    """Three lines for Telegram: when – title, details, link."""
    return f"{when(e, tz)} – {e.title}\n{details(e)}\n{e.url}"
