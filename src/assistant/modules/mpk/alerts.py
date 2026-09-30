"""Telegram alert: time to leave for your next event."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.mpk import service

LEAD = timedelta(minutes=5)  # alert this long before the leave-by time
GRACE = timedelta(minutes=2)  # ...and still alert this long after it (e.g. worker restart)


def format_connection(c: service.Connection, tz: ZoneInfo) -> str:
    icon = "🚋" if c.is_tram else "🚌"
    return (
        f"{icon} {c.line} → {c.headsign} from {c.from_stop} at "
        f"{c.departs.astimezone(tz):%H:%M} (arrives {c.to_stop} {c.arrives.astimezone(tz):%H:%M})"
    )


def leave_now_message(
    trip: service.Trip | None, now: datetime, tz: ZoneInfo
) -> tuple[str, str] | None:
    """(dedupe key, text) if it's time to tell you to leave, else None."""
    if trip is None or trip.leave_by is None or not trip.options or trip.leg.arrive_by is None:
        return None
    if not trip.leave_by - LEAD <= now <= trip.leave_by + GRACE:
        return None
    minutes = round((trip.leave_by - now).total_seconds() / 60)
    when = f"in {minutes} min" if minutes > 0 else "now"
    leg, first = trip.leg, trip.options[0]
    lines = [
        f"🚶 Leave {when} for {leg.title} ({leg.arrive_by.astimezone(tz):%H:%M}, {leg.dest.label})",
        format_connection(first, tz),
    ]
    if len(trip.options) > 1:
        lines.append("or " + format_connection(trip.options[1], tz))
    return f"leave:{leg.title}:{leg.arrive_by.isoformat()}", "\n".join(lines)


class LeaveNowAlert:
    """Runs every minute in the worker; each event gets at most one alert."""

    name = "mpk_alerts"
    schedule = IntervalTrigger(minutes=1)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        trip = service.current_trip(session, now, config)
        if message := leave_now_message(trip, now, config.tz):
            key, text = message
            notify(session, text, key=key)
