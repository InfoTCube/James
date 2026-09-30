from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.birthdays import collector, service
from assistant.modules.calendar.models import CalendarEvent
from assistant.services.bot.replies import reply_birthdays

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})


def at(d: int, h: int = 0) -> datetime:
    return datetime(2026, 10, d, h, tzinfo=TZ)


@pytest.fixture
def db(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        for i, (day, title) in enumerate([(1, "Mateusz's birthday"), (3, "Urodziny Zosi")]):
            s.add(
                CalendarEvent(
                    id=f"b{i}",
                    start=at(day),
                    end=at(day + 1),
                    title=title,
                    location=None,
                    all_day=True,
                    kind="birthday",
                )
            )
    return engine


def test_name_from_title():
    assert service.name_from_title("Mateusz's birthday") == "Mateusz"
    assert service.name_from_title("Ola’s Birthday") == "Ola"
    assert service.name_from_title("Urodziny Zosi") == "Zosi"
    assert service.name_from_title("Party") == "Party"


def test_upcoming_and_bot(db):
    with Session(db) as s:
        assert [(b.name, b.day) for b in service.upcoming(s, at(1, 8), TZ, days=2)] == [
            ("Mateusz", date(2026, 10, 1))
        ]
        assert reply_birthdays(s, at(1, 8), CONFIG, []) == (
            "🎂 Mateusz – today\n🎂 Zosi – Sat 03.10"
        )
        assert reply_birthdays(s, at(5, 8), CONFIG, []) == "No birthdays in the next 14 days."


def test_reminders_evening_before_and_morning_of(db, monkeypatch):
    sent = []
    now = {"t": at(2, 18)}
    monkeypatch.setattr(collector, "utcnow", lambda: now["t"])
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)
    monkeypatch.setattr(collector, "notify", lambda s, text, key: sent.append(text))
    for t in (at(2, 18), at(3, 9), at(3, 18)):
        now["t"] = t
        with Session(db) as s, s.begin():
            collector.BirthdayReminder().run(s)
    assert sent == ["🎂 Zosi's birthday tomorrow!", "🎂 Zosi's birthday today!"]
