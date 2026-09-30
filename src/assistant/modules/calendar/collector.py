"""Google Calendar API → calendar_events."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.modules.calendar import client
from assistant.modules.calendar.models import CalendarEvent

DAYS_AHEAD = 14


def _when(t: dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    """API start/end → (aware datetime, all_day). All-day dates become local midnight."""
    if "dateTime" in t:
        return datetime.fromisoformat(t["dateTime"]), False
    return datetime.combine(date.fromisoformat(t["date"]), time.min, tz), True


def _declined(item: dict) -> bool:
    return any(
        a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees", [])
    )


def parse(items: list[dict], tz: ZoneInfo) -> list[CalendarEvent]:
    """API event items → rows. Skips cancelled events and ones you declined."""
    rows = []
    for item in items:
        if item.get("status") == "cancelled" or _declined(item):
            continue
        start, all_day = _when(item["start"], tz)
        end, _ = _when(item["end"], tz)
        rows.append(
            CalendarEvent(
                id=item["id"],
                start=start,
                end=end,
                title=(item.get("summary") or "").strip() or "(no title)",
                location=(item.get("location") or "").strip() or None,
                all_day=all_day,
            )
        )
    return rows


class CalendarCollector:
    name = "calendar"
    schedule = IntervalTrigger(minutes=15)

    def run(self, session: Session) -> None:
        tz = load_config().tz
        start = datetime.combine(utcnow().astimezone(tz).date(), time.min, tz)
        # fetch first: a failed request raises before the table is touched
        rows = {
            r.id: r for r in parse(client.list_events(start, start + timedelta(DAYS_AHEAD)), tz)
        }
        session.execute(delete(CalendarEvent))  # full replace picks up deleted/moved events
        session.add_all(rows.values())
