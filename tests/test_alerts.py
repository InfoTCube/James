from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import Session

from assistant.core import notifier
from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.calendar.models import CalendarEvent
from assistant.modules.mpk import service
from assistant.modules.mpk.alerts import leave_now_message
from assistant.services.bot.replies import reply_next, reply_today, reply_weather

TZ = ZoneInfo("Europe/Warsaw")
HOME = service.Target("home", ["DWORZEC GŁÓWNY"], 5)
WORK = service.Target("work", ["Rynek"], 5)


def at(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 7, h, m, tzinfo=TZ)


@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path / "db.sqlite")


@pytest.fixture
def telegram(monkeypatch):
    sent = []

    def post(url, json, timeout):
        sent.append(json["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notifier.httpx, "post", post)
    return sent


def test_notify_sends_each_key_once(engine, telegram):
    for _ in range(2):
        with Session(engine) as s, s.begin():
            notifier.notify(s, "leave now", key="leave:x")
    with Session(engine) as s, s.begin():
        notifier.notify(s, "no key: always sent")
    assert telegram == ["leave now", "no key: always sent"]


def test_notify_without_config_does_nothing(engine, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with Session(engine) as s, s.begin():
        assert notifier.notify(s, "hi", key="k") is False
    with Session(engine) as s:  # not recorded: it'll be sent once Telegram is configured
        assert s.get(notifier.SentNotification, "k") is None


def trip(leave_by: datetime) -> service.Trip:
    leg = service.Leg("Praca", HOME, WORK, None, at(8), at(6), at(8))
    tram = service.Connection(
        "22", True, "PILCZYCE", "DWORZEC GŁÓWNY", "Rynek", at(7, 45), at(7, 55)
    )
    return service.Trip(leg, [tram], leave_by, None)


def test_leave_now_window():
    t = trip(leave_by=at(7, 40))
    assert leave_now_message(t, at(7, 34), TZ) is None  # too early
    key, text = leave_now_message(t, at(7, 36), TZ)
    assert key == f"leave:Praca:{at(8).isoformat()}"
    assert text.splitlines() == [
        "🚶 Leave in 4 min for Praca (08:00, work)",
        "🚋 22 → PILCZYCE from DWORZEC GŁÓWNY at 07:45 (arrives Rynek 07:55)",
    ]
    assert leave_now_message(t, at(7, 41), TZ)[1].startswith("🚶 Leave now")
    assert leave_now_message(t, at(7, 43), TZ) is None  # missed it
    assert leave_now_message(None, at(7, 36), TZ) is None


def test_bot_replies_with_empty_db(engine):
    config = Config(location={"latitude": 51.1, "longitude": 17.0})
    with Session(engine) as s:
        assert reply_today(s, at(9), config) == "Nothing in the calendar today."
        assert reply_weather(s, at(9), config) == "No forecast yet."
        assert reply_next(s, at(9), config) == "Nothing planned with a location in the next 7 days."


def test_bot_today_lists_events(engine):
    config = Config(location={"latitude": 51.1, "longitude": 17.0})
    with Session(engine) as s, s.begin():
        s.add(
            CalendarEvent(
                id="1",
                start=at(8),
                end=at(16),
                title="Praca",
                location="work",
                all_day=False,
                kind="default",
            )
        )
    with Session(engine) as s:
        assert reply_today(s, at(9), config) == "08:00–16:00  Praca · work"


def test_help_lists_every_command():
    from assistant.services.bot.__main__ import COMMANDS, help_text

    assert help_text().splitlines() == [
        *(f"/{name} – {desc}" for name, (_, desc) in COMMANDS.items()),
        "/help – This list",
    ]
    assert set(COMMANDS) == {"next", "today", "weather"}
