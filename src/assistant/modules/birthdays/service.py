"""Upcoming birthdays, from Google Calendar's contact birthdays. No network here."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from assistant.modules.calendar import service as calendar

TITLE_PATTERNS = [
    r"^(.+?)['’]s birthday$",  # Google, English: "Mateusz's birthday"
    r"^urodziny\s+(.+)$",  # Google, Polish: "Urodziny Zosi"
]


@dataclass
class Birthday:
    name: str
    day: date


def name_from_title(title: str) -> str:
    """Person's name from a birthday event title; anything unrecognised as is."""
    for pattern in TITLE_PATTERNS:
        if m := re.match(pattern, title.strip(), re.IGNORECASE):
            return m[1]
    return title.strip()


def upcoming(session: Session, now: datetime, tz: ZoneInfo, days: int = 7) -> list[Birthday]:
    """Birthdays from today (local) for `days` days, soonest first."""
    start = datetime.combine(now.astimezone(tz).date(), time.min, tz)
    events = calendar.birthdays_between(session, start, start + timedelta(days=days))
    return [Birthday(name_from_title(e.title), e.start.astimezone(tz).date()) for e in events]


def when(day: date, today: date) -> str:
    """Day label: "today", "tomorrow" or e.g. "Fri 02.10"."""
    delta = (day - today).days
    return "today" if delta == 0 else "tomorrow" if delta == 1 else f"{day:%a %d.%m}"
