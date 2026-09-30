"""Calendar reads. No network here."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import WEEKDAYS, LocationRule, fold, load_config
from assistant.modules.calendar.models import CalendarEvent


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    all_day: bool = False


def _location(e: CalendarEvent, tz: ZoneInfo, rules: list[LocationRule]) -> str | None:
    """The event's own location, else the place alias from the first matching rule."""
    if e.location:
        return e.location
    day = WEEKDAYS[e.start.astimezone(tz).weekday()]
    return next((r.place for r in rules if day in r.days and fold(r.title) in fold(e.title)), None)


def events_between(
    session: Session,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    rules: list[LocationRule] | None = None,
) -> list[Event]:
    """Events overlapping [start, end), ordered by start (all-day first), with location rules
    applied. Birthdays are left out; they're stored for the birthdays module."""
    rules = load_config().location_rules if rules is None else rules
    q = select(CalendarEvent).where(
        CalendarEvent.start < end, CalendarEvent.end > start, CalendarEvent.kind != "birthday"
    )
    q = q.order_by(CalendarEvent.start, CalendarEvent.all_day.desc())
    return [
        Event(e.title, e.start, e.end, _location(e, tz, rules), e.all_day)
        for e in session.scalars(q)
    ]


def day_events(
    session: Session, day: datetime, tz: ZoneInfo, rules: list[LocationRule] | None = None
) -> list[Event]:
    """All events on the local calendar day containing `day`."""
    start = datetime.combine(day.astimezone(tz).date(), time.min, tz)
    return events_between(session, start, start + timedelta(days=1), tz, rules)


def outdoor_events(
    session: Session, now: datetime, tz: ZoneInfo, rules: list[LocationRule] | None = None
) -> list[tuple[datetime, datetime]]:
    """Today's timed events with a location, i.e. ones you leave home for, as (start, end)."""
    events = day_events(session, now, tz, rules)
    return [(e.start, e.end) for e in events if e.location and not e.all_day]


def next_event_with_location(
    session: Session, now: datetime, tz: ZoneInfo, rules: list[LocationRule] | None = None
) -> Event | None:
    """The next timed event with a location that hasn't started yet (looks 7 days ahead)."""
    upcoming = events_between(session, now, now + timedelta(days=7), tz, rules)
    return next((e for e in upcoming if e.start > now and e.location and not e.all_day), None)


def birthdays_between(session: Session, start: datetime, end: datetime) -> list[CalendarEvent]:
    """Google's contact birthdays (all-day events) starting in [start, end), by date."""
    q = select(CalendarEvent).where(
        CalendarEvent.kind == "birthday", CalendarEvent.start >= start, CalendarEvent.start < end
    )
    return list(session.scalars(q.order_by(CalendarEvent.start)))
