"""Reply texts for bot commands. Plain functions over the DB, no Telegram here."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.alerts import format_connection
from assistant.modules.weather import service as weather


def reply_next(session: Session, now: datetime, config: Config) -> str:
    """/next: the trip you need now, else your next event with a location."""
    tz = config.tz
    trip = mpk.current_trip(session, now, config)
    if trip:
        leg = trip.leg
        where = leg.title if leg.arrive_by else "home"
        head = f"{leg.origin.label if leg.origin else '?'} → {where}"
        if leg.arrive_by:
            head += f" ({leg.arrive_by.astimezone(tz):%H:%M})"
        lines = [head]
        if trip.timetable_end:
            lines.append(f"⚠️ Timetable ended {trip.timetable_end:%d.%m}: last week's times")
        if trip.leave_by:
            lines.append(f"Leave by {trip.leave_by.astimezone(tz):%H:%M}")
        lines += [format_connection(c, tz) for c in trip.options] or ["No direct connection found"]
        return "\n".join(lines)

    event = calendar.next_event_with_location(session, now, tz)
    if event is None:
        return "Nothing planned with a location in the next 7 days."
    shows = event.start - mpk.LOOKAHEAD
    return (
        f"Next: {event.title}, {event.start.astimezone(tz):%a %d.%m %H:%M} at {event.location}.\n"
        f"Connections show up from {shows.astimezone(tz):%H:%M}."
    )


def reply_today(session: Session, now: datetime, config: Config) -> str:
    """/today: today's events."""
    tz = config.tz
    events = calendar.day_events(session, now, tz)
    if not events:
        return "Nothing in the calendar today."
    lines = []
    for e in events:
        when = (
            "all day"
            if e.all_day
            else f"{e.start.astimezone(tz):%H:%M}–{e.end.astimezone(tz):%H:%M}"
        )
        lines.append(f"{when}  {e.title}" + (f" · {e.location}" if e.location else ""))
    return "\n".join(lines)


def reply_weather(session: Session, now: datetime, config: Config) -> str:
    """/weather: now + what to wear for today's time outside."""
    tz = config.tz
    hours = weather.get_hours(session, now - timedelta(hours=1), now + timedelta(hours=1))
    if not hours:
        return "No forecast yet."
    h = hours[0]
    label, icon = weather.describe(h.code)
    lines = [f"{icon} {h.temp:.0f}° {label}, feels {h.feels_like:.0f}°"]
    advice = weather.get_clothing_advice(
        session, now, tz, calendar.outdoor_events(session, now, tz)
    )
    if advice:
        lines.append(f"👕 {advice.layers}")
        lines += advice.extras
    return "\n".join(lines)
