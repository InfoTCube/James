import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.parcels import client, collector, service
from assistant.services.bot import replies

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 11, 11, 15, tzinfo=TZ)
FIXTURES = Path("tests/fixtures/parcels")
NUMBER = "620000011234567890123456"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def session(tmp_path):
    with Session(make_engine(tmp_path / "db.sqlite")) as s:
        yield s


def test_parse():
    t = client.parse(fixture("tracking.json"))
    assert t.status == "ready_to_pickup"
    assert t.status_at == datetime(2026, 11, 11, 14, 2, 11, tzinfo=TZ)
    assert t.locker == "WRO118M, Kościuszki 70 (Przy sklepie Żabka)"
    assert client.parse({"status": "confirmed", "tracking_details": []}).locker is None
    titles = client.parse_titles(fixture("statuses.json"))
    assert titles["ready_to_pickup"] and "." not in titles["ready_to_pickup"][-1:]


def test_find_numbers():
    assert service.find_numbers(f"Twoja paczka {NUMBER} jest w drodze") == [NUMBER]
    assert service.find_numbers("6200 0001 1234 5678 9012 3456") == [NUMBER]
    assert service.find_numbers("order 12345, call 600123456") == []


def test_track_alerts_once_and_forgets_delivered(session, monkeypatch):
    sent, answers = [], {NUMBER: client.parse(fixture("tracking.json"))}
    monkeypatch.setattr(collector.client, "track", lambda n: answers.get(n))
    monkeypatch.setattr(collector.client, "status_titles", lambda: {"ready_to_pickup": "Ready"})
    monkeypatch.setattr(collector, "notify", lambda s, text, key: sent.append(key))
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)

    service.add(session, NUMBER, "headphones", NOW)
    service.add(session, "1" * 24, "", NOW - timedelta(days=1))  # unknown to InPost (yet)
    session.flush()
    collector.ParcelsCollector().check(session)
    collector.ParcelsCollector().check(session)  # unchanged: no second alert
    assert sent == [f"parcel:{NUMBER}:ready_to_pickup"]
    assert [service.format_parcel(p, TZ) for p in service.parcels(session)] == [
        "📦 headphones: Ready (11.11 14:02)\n   WRO118M, Kościuszki 70 (Przy sklepie Żabka)",
        "📦 …111111: not in InPost's system yet",
    ]

    p = session.get(service.Parcel, NUMBER)
    p.status = "delivered"
    service.forget_done(session, p.status_at + timedelta(days=3))
    assert [x.number for x in service.parcels(session)] == ["1" * 24]


def test_bot_track_and_untrack(session, monkeypatch):
    monkeypatch.setattr(replies.ParcelsCollector, "check", lambda self, s, alert=True: None)
    assert replies.reply_track(session, NOW, CONFIG, []).startswith("No parcels tracked")
    assert replies.reply_track(session, NOW, CONFIG, ["hello"]).startswith("Usage")
    assert replies.reply_track(session, NOW, CONFIG, [NUMBER, "new", "shoes"]) == (
        "📦 new shoes: not in InPost's system yet"
    )
    assert replies.reply_untrack(session, NOW, CONFIG, ["123456"]) == "🗑️ Stopped tracking new shoes"
    assert replies.reply_untrack(session, NOW, CONFIG, ["123456"]) == "No such parcel."
