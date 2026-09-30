import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import Session

from assistant.core.config import LocationRule
from assistant.core.db import make_engine
from assistant.modules.calendar import client, collector, service
from assistant.modules.calendar.collector import CalendarCollector, parse
from assistant.modules.calendar.models import CalendarEvent

TZ = ZoneInfo("Europe/Warsaw")
OFFICE = LocationRule(title="praca", days=["wed"], place="work")
ITEMS = json.loads(Path("tests/fixtures/calendar/events.json").read_text(encoding="utf-8"))["items"]


def local(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


def test_parse_skips_cancelled_and_declined():
    rows = {r.id: r for r in parse(ITEMS, TZ)}
    assert set(rows) == {
        "class1_20261005T080000Z",
        "class2_20261005T101500Z",
        "call1",
        "bday1",
        "class1_20261019T080000Z",
    }
    c = rows["class1_20261005T080000Z"]
    assert c.start == local(5, 10) and c.location.startswith("Politechnika Wrocławska")
    assert (
        rows["call1"].start == datetime(2026, 10, 5, 16, tzinfo=UTC) and not rows["call1"].location
    )
    assert rows["bday1"].all_day and rows["bday1"].start == local(5, 0)
    assert rows["bday1"].kind == "birthday" and c.kind == "default"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    monkeypatch.setattr(collector, "utcnow", lambda: local(5, 7))
    monkeypatch.setattr(client, "list_events", lambda start, end: ITEMS)
    with Session(engine) as s, s.begin():
        CalendarCollector().run(s)
    return engine


def test_outdoor_events_are_timed_with_location(db):
    with Session(db) as s:
        assert service.outdoor_events(s, local(5, 7), TZ, rules=[]) == [
            (local(5, 10), local(5, 12)),
            (local(5, 12, 15), local(5, 14)),
        ]
        titles = [e.title for e in service.day_events(s, local(5, 7), TZ, rules=[])]
        assert "Urodziny Zosi" not in titles and titles[0] == "Algorytmy i struktury danych"
        nxt = service.next_event_with_location(s, local(5, 11), TZ, rules=[])
        assert nxt.title == "Sieci komputerowe"


def test_location_rule_sends_work_to_office_only_on_office_days(db):
    with Session(db) as s, s.begin():
        for d in (6, 7):  # Tuesday (home office), Wednesday (office)
            s.add(
                CalendarEvent(
                    id=f"work{d}",
                    start=local(d, 8),
                    end=local(d, 16),
                    title="Praca 💼",
                    location=None,
                    all_day=False,
                    kind="default",
                )
            )
    with Session(db) as s:
        assert service.outdoor_events(s, local(6, 7), TZ, rules=[OFFICE]) == []
        wed = service.day_events(s, local(7, 7), TZ, rules=[OFFICE])
        assert [(e.title, e.location) for e in wed] == [("Praca 💼", "work")]
        nxt = service.next_event_with_location(s, local(5, 15), TZ, rules=[OFFICE])
        assert (nxt.title, nxt.start) == ("Praca 💼", local(7, 8))


def test_rerun_replaces_everything(db, monkeypatch):
    monkeypatch.setattr(client, "list_events", lambda start, end: ITEMS[:1])
    with Session(db) as s, s.begin():
        CalendarCollector().run(s)
    with Session(db) as s:
        assert len(service.events_between(s, local(1, 0), local(31, 0), TZ, rules=[])) == 1


def test_add_event_posts_to_google(monkeypatch):
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"], sent["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"id": "new1"})

    monkeypatch.setattr(
        client,
        "_client",
        lambda: httpx.Client(base_url=client.API, transport=httpx.MockTransport(handler)),
    )
    assert client.add_event("Dentysta", local(9, 15), local(9, 16), "Legnicka 5")["id"] == "new1"
    assert sent["url"].endswith("/calendars/primary/events")
    assert sent["body"]["start"]["dateTime"] == "2026-10-09T15:00:00+02:00"
    assert sent["body"]["location"] == "Legnicka 5"
