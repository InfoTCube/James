"""Calendar reads. No network here."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.modules.calendar.models import CalendarEvent


def events_between(session: Session, start: datetime, end: datetime) -> list[CalendarEvent]:
    """Events overlapping [start, end), ordered by start (all-day first on the same day)."""
    q = select(CalendarEvent).where(CalendarEvent.start < end, CalendarEvent.end > start)
    return list(session.scalars(q.order_by(CalendarEvent.start, CalendarEvent.all_day.desc())))


def day_events(session: Session, day: datetime, tz: ZoneInfo) -> list[CalendarEvent]:
    """All events on the local calendar day containing `day`."""
    start = datetime.combine(day.astimezone(tz).date(), time.min, tz)
    return events_between(session, start, start + timedelta(days=1))


def outdoor_events(
    session: Session, now: datetime, tz: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    """Today's timed events with a location, i.e. ones you leave home for, as (start, end)."""
    return [(e.start, e.end) for e in day_events(session, now, tz) if e.location and not e.all_day]


def next_event_with_location(session: Session, now: datetime) -> CalendarEvent | None:
    """The next timed event with a location that hasn't started yet."""
    q = (
        select(CalendarEvent)
        .where(CalendarEvent.start > now, CalendarEvent.location.is_not(None))
        .where(CalendarEvent.all_day.is_(False))
        .order_by(CalendarEvent.start)
    )
    return session.scalars(q.limit(1)).first()
