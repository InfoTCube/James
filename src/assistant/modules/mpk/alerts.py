"""Telegram alert: time to leave for your next event."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.mpk import service
from assistant.modules.mpk.live import add_live_delays

LEAD = timedelta(minutes=5)  # alert this long before the leave-by time
GRACE = timedelta(minutes=2)  # ...and still alert this long after it (e.g. worker restart)
POLL_FROM = timedelta(minutes=15)  # start reading live positions this long before leave-by


def format_connection(c: service.Connection, tz: ZoneInfo) -> str:
    icon = "🚋" if c.is_tram else "🚌"
    live = ""
    if c.delay_min:
        live = f" ~{c.delay_min:+d} min"
    elif c.delay_min == 0:
        live = " (on time)"
    arrival = ("~" if c.delay_min else "") + f"{c.expected_arrival.astimezone(tz):%H:%M}"
    return (
        f"{icon} {c.line} → {c.headsign} from {c.from_stop} at "
        f"{c.departs.astimezone(tz):%H:%M}{live} (arrives {c.to_stop} {arrival})"
    )


def effective_leave_by(trip: service.Trip) -> datetime | None:
    """Leave-by time moved by the first option's live delay estimate, if there is one."""
    if trip.leave_by is None or not trip.options:
        return None
    return trip.leave_by + timedelta(minutes=trip.options[0].delay_min or 0)


def leave_now_message(
    trip: service.Trip | None, now: datetime, tz: ZoneInfo
) -> tuple[str, str] | None:
    """(dedupe key, text) if it's time to tell you to leave, else None."""
    if trip is None or trip.leg.arrive_by is None:
        return None
    leave_by = effective_leave_by(trip)
    if leave_by is None or not leave_by - LEAD <= now <= leave_by + GRACE:
        return None
    minutes = round((leave_by - now).total_seconds() / 60)
    when = f"in {minutes} min" if minutes > 0 else "now"
    leg, first = trip.leg, trip.options[0]
    head = (
        f"🚶 Leave {when} for {leg.title} ({leg.arrive_by.astimezone(tz):%H:%M}, {leg.dest.label})"
    )
    if trip.late_min:
        head += f". You'll be ~{trip.late_min} min late"
    lines = [
        head,
        format_connection(first, tz),
    ]
    if len(trip.options) > 1:
        lines.append("or " + format_connection(trip.options[1], tz))
    return f"leave:{leg.title}:{leg.arrive_by.isoformat()}", "\n".join(lines)


class LeaveNowAlert:
    """Runs every minute in the worker; each event gets at most one alert.
    From POLL_FROM before leave-by it also reads live positions (its own tracker), so the
    alert can move with the tram's estimated delay."""

    name = "mpk_alerts"
    schedule = IntervalTrigger(minutes=1)

    def __init__(self) -> None:
        self.tracker = service.Tracker()

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        trip = service.current_trip(session, now, config)
        if trip and trip.leave_by and trip.options and now >= trip.leave_by - POLL_FROM:
            add_live_delays(session, trip.options, now, self.tracker)
        if message := leave_now_message(trip, now, config.tz):
            key, text = message
            notify(session, text, key=key)
