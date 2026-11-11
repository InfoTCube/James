"""Kitchen timers: set, list, ring, stop. The dashboard rings them. No network here."""

import re
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.modules.timers.models import Timer

RING_FOR = timedelta(minutes=3)  # then it stops by itself
MAX = timedelta(hours=24)
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
         "fifty": 50, "sixty": 60, "ninety": 90}  # fmt: skip
UNITS = {"h": 3600, "hour": 3600, "hours": 3600, "m": 60, "min": 60, "mins": 60, "minute": 60,
         "minutes": 60, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}  # fmt: skip
PART = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?|" + "|".join(sorted(WORDS, key=len, reverse=True)) + r")"
    r"(?P<half> and a half)?\s*(?P<unit>"
    + "|".join(sorted(UNITS, key=len, reverse=True))
    + r")(?![a-z])"
    r"(?P<half2> and a half)?",
    re.IGNORECASE,
)


def parse_duration(text: str) -> timedelta | None:
    """ "10 minutes", "1 hour 30 minutes", "an hour and a half", "half an hour", "90 s",
    "5m" → a duration; None if there's none (or it's over 24 h)."""
    text = text.lower()
    seconds = 0.0
    if re.search(r"\bhalf an hour\b", text):
        seconds += 1800
        text = re.sub(r"\bhalf an hour\b", "", text)
    for m in PART.finditer(text):
        n = m["n"]
        amount = WORDS[n] if n in WORDS else float(n.replace(",", "."))
        if m["half"] or m["half2"]:
            amount += 0.5
        seconds += amount * UNITS[m["unit"]]
    if not 0 < seconds <= MAX.total_seconds():
        return None
    return timedelta(seconds=round(seconds))


FILLER = re.compile(
    r"\b(set|start|put|on|a|an|the|my|timer|for|please|me|and|half|hour|of|in|up)\b", re.I
)


def parse(text: str) -> tuple[timedelta | None, str]:
    """ "set a timer for 10 minutes for the pasta", "10m pasta" → (10 min, "pasta")."""
    rest = PART.sub(" ", re.sub(r"\bhalf an hour\b", " ", text, flags=re.I))
    label = " ".join(FILLER.sub(" ", rest).strip(" .,!?").split())
    return parse_duration(text), label


def add(session: Session, length: timedelta, label: str, now: datetime) -> Timer:
    """Start a timer."""
    timer = Timer(label=label.strip(), ends_at=now + length)
    session.add(timer)
    session.flush()
    return timer


def running(session: Session, now: datetime) -> list[Timer]:
    """Timers still counting down, soonest first."""
    q = select(Timer).where(Timer.ends_at > now, Timer.stopped.is_(False))
    return list(session.scalars(q.order_by(Timer.ends_at)))


def ringing(session: Session, now: datetime) -> list[Timer]:
    """Timers that are done and not stopped yet (for at most RING_FOR)."""
    q = select(Timer).where(
        Timer.ends_at <= now, Timer.ends_at > now - RING_FOR, Timer.stopped.is_(False)
    )
    return list(session.scalars(q.order_by(Timer.ends_at)))


def stop(session: Session, now: datetime, which: str = "") -> list[Timer]:
    """Stop ringing timers, or else cancel running ones: by label (or its start), or all with
    no `which`. The ones stopped. Old timers are cleaned up here too."""
    session.execute(delete(Timer).where(Timer.ends_at < now - timedelta(days=1)))
    targets = ringing(session, now) or running(session, now)
    which = which.strip().lower()
    if which and which not in ("all", "the", "timer", "timers"):
        targets = [t for t in targets if t.label.lower().startswith(which)]
    for t in targets:
        t.stopped = True
    session.flush()
    return targets


def left(timer: Timer, now: datetime) -> str:
    """Time left, spoken style: "4 minutes 30 seconds", "1 hour 5 minutes", "20 seconds"."""
    s = max(0, round((timer.ends_at - now).total_seconds()))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    parts = [(h, "hour"), (m, "minute"), (sec if h == 0 else 0, "second")]
    said = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in parts if n]
    return " ".join(said) or "0 seconds"


def describe(timer: Timer, now: datetime) -> str:
    """E.g. "pasta timer: 4 minutes 30 seconds left"."""
    name = f"{timer.label} timer" if timer.label else "Timer"
    return f"{name}: {left(timer, now)} left"
