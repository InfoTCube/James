"""Alarms and the morning briefing. The add/list/cancel functions are what the bot uses now
and voice commands will use later. No network here."""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import AlarmConfig, Config
from assistant.modules.alarm.models import Alarm
from assistant.modules.birthdays import service as birthdays
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.alerts import format_connection
from assistant.modules.weather import service as weather

RING_GRACE = timedelta(minutes=10)  # still ring this late (e.g. after a worker restart)
RING_FOR = timedelta(minutes=3)  # ring this long if nobody stops it, then start the briefing
SNOOZE = timedelta(minutes=10)
BRIEFING_FOR = timedelta(minutes=3)  # the dashboard shows the briefing this long after stopping


# --- voice/bot API ------------------------------------------------------------------------------


def parse_time(text: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    """Parse "7:30", "07.30", "730" or "7" into the next time it's that o'clock."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.]?(\d{2}))?\s*", text)
    if not m or int(m[1]) > 23 or int(m[2] or 0) > 59:
        return None
    local = now.astimezone(tz)
    at = datetime.combine(local.date(), time(int(m[1]), int(m[2] or 0)), tz)
    return at if at > now else at + timedelta(days=1)


def add_alarm(session: Session, at: datetime, label: str | None = None) -> Alarm:
    """Set a one-off alarm."""
    alarm = Alarm(at=at, label=label, source="manual")
    session.add(alarm)
    session.flush()  # assigns the id
    return alarm


def upcoming_alarms(session: Session, now: datetime) -> list[Alarm]:
    """Alarms that will still ring, soonest first."""
    q = select(Alarm).where(
        Alarm.at >= now - RING_GRACE, Alarm.cancelled.is_(False), Alarm.rung_at.is_(None)
    )
    return list(session.scalars(q.order_by(Alarm.at)))


def cancel_alarm(session: Session, alarm_id: int) -> Alarm | None:
    """Cancel an alarm (automatic ones stay cancelled even if the calendar changes)."""
    alarm = session.get(Alarm, alarm_id)
    if alarm is None or alarm.rung_at is not None or alarm.cancelled:
        return None
    alarm.cancelled = True
    return alarm


def ringing_alarm(session: Session, now: datetime) -> Alarm | None:
    """The alarm ringing right now, if any."""
    q = select(Alarm).where(
        Alarm.rung_at.is_not(None), Alarm.rung_at > now - RING_FOR, Alarm.stopped_at.is_(None)
    )
    return session.scalars(q.order_by(Alarm.rung_at.desc())).first()


def timed_out_alarms(session: Session, now: datetime) -> list[Alarm]:
    """Alarms that rang for RING_FOR without anyone stopping them."""
    q = select(Alarm).where(
        Alarm.rung_at.is_not(None), Alarm.rung_at <= now - RING_FOR, Alarm.stopped_at.is_(None)
    )
    return list(session.scalars(q))


def briefing_alarm(session: Session, now: datetime) -> Alarm | None:
    """The alarm whose briefing is on the dashboard now (stopped less than BRIEFING_FOR ago)."""
    q = select(Alarm).where(Alarm.stopped_at > now - BRIEFING_FOR)
    return session.scalars(q.order_by(Alarm.stopped_at.desc())).first()


def stop(alarm: Alarm, now: datetime) -> bool:
    """Stop a ringing alarm (the briefing follows). False if it isn't ringing."""
    if alarm.rung_at is None or alarm.stopped_at is not None:
        return False
    alarm.stopped_at = now
    return True


def snooze(alarm: Alarm, now: datetime) -> bool:
    """Ring again in SNOOZE. False if it isn't ringing."""
    if alarm.rung_at is None or alarm.stopped_at is not None:
        return False
    alarm.at, alarm.rung_at, alarm.snoozes = now + SNOOZE, None, (alarm.snoozes or 0) + 1
    return True


def due_alarms(session: Session, now: datetime) -> list[Alarm]:
    """Alarms that should ring now."""
    q = select(Alarm).where(
        Alarm.at <= now,
        Alarm.at > now - RING_GRACE,
        Alarm.cancelled.is_(False),
        Alarm.rung_at.is_(None),
    )
    return list(session.scalars(q.order_by(Alarm.at)))


# --- automatic alarm ----------------------------------------------------------------------------


def auto_alarm_time(
    departure: mpk.Departure | None, cfg: AlarmConfig, tz: ZoneInfo
) -> datetime | None:
    """Wake-up time for a day: before_leaving_minutes before you leave, if earlier than `latest`."""
    if departure is None:
        return None
    at = departure.leave_by - timedelta(minutes=cfg.before_leaving_minutes)
    return at if at.astimezone(tz).time() < cfg.latest else None


def plan_auto_alarm(session: Session, day: date, config: Config) -> Alarm | None:
    """Create, move or remove the automatic alarm for `day` to match the calendar."""
    departure = mpk.first_departure(session, day, config)
    at = auto_alarm_time(departure, config.alarm, config.tz)
    alarm = session.scalar(select(Alarm).where(Alarm.source == "auto", Alarm.day == day))
    if alarm and (alarm.cancelled or alarm.rung_at or alarm.snoozes):
        return alarm  # you decided, or it already rang
    if at is None:
        if alarm:
            session.delete(alarm)
        return None
    label = f"{departure.leg.title}, leave by {departure.leave_by.astimezone(config.tz):%H:%M}"
    if alarm is None:
        alarm = Alarm(source="auto", day=day, at=at, label=label)
        session.add(alarm)
    else:
        alarm.at, alarm.label = at, label
    return alarm


# --- morning briefing ---------------------------------------------------------------------------


def briefing(session: Session, now: datetime, config: Config) -> str:
    """Good-morning summary: weather and what to wear, when to leave, today's events."""
    tz = config.tz
    local = now.astimezone(tz)
    greeting = "morning" if local.hour < 12 else "afternoon" if local.hour < 18 else "evening"
    lines = [f"⏰ Good {greeting}! It's {local:%H:%M}."]

    hours = weather.get_hours(session, now - timedelta(hours=1), now + timedelta(hours=1))
    if hours:
        label, icon = weather.describe(hours[0].code)
        line = f"{icon} {hours[0].temp:.0f}° {label}"
        advice = weather.get_clothing_advice(
            session, now, tz, calendar.outdoor_events(session, now, tz)
        )
        if advice:
            line += f" · 👕 {advice.layers}"
        lines.append(line)
        lines += advice.extras if advice else []

    departure = mpk.first_departure(session, now.astimezone(tz).date(), config, after=now)
    if departure:
        leg = departure.leg
        lines.append(
            f"🚶 Leave by {departure.leave_by.astimezone(tz):%H:%M} for {leg.title} "
            f"({leg.arrive_by.astimezone(tz):%H:%M})"
        )
        if departure.connection:
            lines.append(format_connection(departure.connection, tz))

    today = now.astimezone(tz).date()
    for b in birthdays.upcoming(session, now, tz, days=2):
        lines.append(f"🎂 {b.name}'s birthday {birthdays.when(b.day, today)}")

    events = [e for e in calendar.day_events(session, now, tz) if e.end > now]
    if events:
        lines.append("📅 Today:")
        for e in events:
            when = "all day" if e.all_day else f"{e.start.astimezone(tz):%H:%M}"
            lines.append(f"  {when} {e.title}" + (f" · {e.location}" if e.location else ""))
    else:
        lines.append("📅 Nothing in the calendar today.")
    return "\n".join(lines)
