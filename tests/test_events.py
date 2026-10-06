import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config, EventsConfig
from assistant.core.db import make_engine
from assistant.modules.events import collector, service
from assistant.modules.events.models import Event
from assistant.services.bot.replies import reply_events

FIXTURES = Path(__file__).parent / "fixtures" / "events"
NOW = datetime(2026, 10, 6, 10, tzinfo=UTC)
TODAY = date(2026, 10, 6)
TZ = ZoneInfo("Europe/Warsaw")
CFG = EventsConfig(cities=["Wrocław"], keywords=[".net", "hackathon"], exclude=["hr", "startup"])


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def event(**kw) -> Event:
    base = dict(
        id="x:1", source="x", title="Wroc.NET #171", url="u", kind="meetup", topics="IT, .NET",
        city="Wrocław", online=False, starts=date(2026, 10, 20), ends=date(2026, 10, 20),
        at=None, free=True,
    )  # fmt: skip
    return Event(**base | kw)


def test_parse_crossweb():
    events = collector.parse_crossweb(load("crossweb_wroclaw.html"), TODAY)
    e = next(e for e in events if e.id == "crossweb:kongres-cyberbezpieczenstwa-cybertrust-2026")
    assert (e.title, e.kind, e.city, e.online, e.free) == (
        "Kongres Cyberbezpieczeństwa CyberTrust 2026", "conference", "Wrocław", False, False
    )  # fmt: skip
    assert (e.starts, e.ends) == (date(2026, 10, 20), date(2026, 10, 21))
    assert e.url == "https://crossweb.pl/wydarzenia/kongres-cyberbezpieczenstwa-cybertrust-2026/"
    assert e.topics == "IT, Zarządzanie projektami, security, AI/ML, Call For Papers"
    assert events[-2].starts == date(2027, 6, 15)  # no year in the list: next June
    lean = next(
        e for e in events if e.id == "crossweb:xxvi-konferencja-i-love-lean-pazdziernik-2026"
    )
    assert (lean.city, lean.online) == ("Wrocław", True)  # "Wrocław Online"
    online = collector.parse_crossweb(load("crossweb_online.html"), TODAY)
    hack = next(e for e in online if e.id == "crossweb:colosseum-hackathon-wrzesien-2026")
    assert (hack.kind, hack.city, hack.online) == ("hackathon", "", True)
    assert hack.topics == "IT, Startup, programowanie, Blockchain, Rust, Fintech"


def test_parse_meetup():
    events = collector.parse_meetup(load("meetup.html"), TZ)
    assert len(events) == 12  # stubs without a title skipped
    e = next(e for e in events if e.id == "meetup:316767368")
    assert e.title == "171. spotkanie Wroc.NET x Devstyle (Aniserowicz, Kubryński)"
    assert (e.topics, e.city, e.online) == ("Wrocław .NET User Group", "Wrocław", False)
    assert e.at == datetime(2026, 10, 20, 16, tzinfo=UTC) and e.starts == date(2026, 10, 20)
    assert e.url == "https://www.meetup.com/wrocnet/events/316767368/"
    htb = next(e for e in events if e.id == "meetup:316570872")
    assert (htb.city, htb.online) == ("", True)
    with pytest.raises(ValueError):
        collector.parse_meetup("<html>blocked</html>", TZ)


def test_parse_devpost():
    events = collector.parse_devpost(json.loads(load("devpost.json")))
    assert len(events) == 40
    e = events[0]
    assert (e.title, e.kind, e.online, e.city) == (
        "Build, Ship, Shape: Amazon Developer Hackathon", "hackathon", True, ""
    )  # fmt: skip
    assert (e.starts, e.ends) == (date(2026, 8, 31), date(2026, 10, 23))
    assert e.topics.startswith("Machine Learning/AI, Open Ended")
    assert any(not e.online and e.city for e in events)  # in person somewhere


def test_devpost_days():
    days = collector.devpost_days
    assert days("Oct 06 - 12, 2026") == (date(2026, 10, 6), date(2026, 10, 12))
    assert days("Dec 31, 2026") == (date(2026, 12, 31), date(2026, 12, 31))
    assert days("Dec 15 - Jan 10, 2027") == (date(2026, 12, 15), date(2027, 1, 10))
    assert days("Dec 15, 2026 - Jan 10, 2027") == (date(2026, 12, 15), date(2027, 1, 10))


def test_parse_mlh():
    events = collector.parse_mlh(load("mlh.html"), TZ)
    e = events[0]
    assert (e.id, e.title, e.city, e.online) == (
        "mlh:hacknc-0e", "HackNC", "Chapel Hill, North Carolina", False
    )  # fmt: skip
    assert (e.starts, e.ends, e.url) == (
        date(2026, 10, 9),
        date(2026, 10, 11),
        "https://hacknc.com/",
    )
    with pytest.raises(ValueError):
        collector.parse_mlh("<html></html>", TZ)


def test_matches():
    assert service.matches(event(title="Wrocław Data Community", topics="IT, bazy danych"), CFG)
    assert service.matches(event(city="Wroclaw"), CFG)  # diacritics ignored
    assert not service.matches(event(title="Pitch MeetUp", topics="Startup"), CFG)  # excluded
    assert not service.matches(event(city="Warszawa", topics="IT, Java"), CFG)
    online = dict(city="", online=True)
    assert service.matches(event(**online), CFG)  # .NET in topics
    assert service.matches(event(**online, title="GrowUp", kind="hackathon", topics="IT"), CFG)
    assert not service.matches(event(**online, title="AI Book Club", topics="AI/ML"), CFG)
    assert not service.matches(event(**online), CFG.model_copy(update={"online": False}))


def test_format_event():
    e = event(at=datetime(2026, 10, 20, 16, tzinfo=UTC))
    assert (
        service.format_event(e, TZ) == "Tue 20.10 18:00 – Wroc.NET #171\nWrocław · meetup · free\nu"
    )
    long = event(ends=date(2026, 10, 21), city="", online=True, free=None, kind="conference")
    assert service.when(long, TZ) == "Tue 20.10–Wed 21.10"
    assert service.details(long) == "online · conference"


def copy(e: Event) -> Event:
    skip = ("dedupe_key", "first_seen")
    return Event(**{c.key: getattr(e, c.key) for c in Event.__table__.c if c.key not in skip})


@pytest.fixture
def run_events(tmp_path, monkeypatch):
    """Run the collector over fake sites {name: events or an exception}; returns sent texts."""
    engine = make_engine(tmp_path / "db.sqlite")
    sent = []
    monkeypatch.setattr(collector, "utcnow", lambda: NOW)
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text))
    cfg = Config(location={"latitude": 51.1, "longitude": 17.0}, events=CFG.model_dump())
    monkeypatch.setattr(collector, "load_config", lambda: cfg)

    def run(sites: dict) -> list[str]:
        def fetcher(result):
            def fetch(cfg, today, tz):
                if isinstance(result, Exception):
                    raise result
                return [copy(e) for e in result]  # fresh rows each run, like a real fetch

            return fetch

        sources = {name: (name, fetcher(result)) for name, result in sites.items()}
        monkeypatch.setattr(collector, "SOURCES", sources)
        with Session(engine) as s, s.begin():
            collector.EventsCollector().run(s)
        return sent

    run.engine = engine
    return run


def test_collector_alerts_once_without_duplicates(run_events):
    sites = {
        "meetup": [event(id="meetup:1", at=datetime(2026, 10, 20, 16, tzinfo=UTC))],
        "crossweb": [
            event(id="crossweb:a", title="WROC.NET #171"),  # same event as meetup:1
            event(  # already running: too late
                id="crossweb:b", title="Running", starts=date(2026, 10, 1), ends=date(2026, 10, 9)
            ),
            event(id="crossweb:c", title="HR Days", topics="HR"),
        ],
    }
    sent = run_events(sites)
    assert sent == ["📅 1 new event\n\nTue 20.10 18:00 – Wroc.NET #171\nWrocław · meetup · free\nu"]
    run_events(sites)
    assert len(sent) == 1  # nothing new the second time
    with Session(run_events.engine) as s:
        assert [e.id for e in service.upcoming(s, TODAY)] == ["meetup:1"]
        cfg = Config(location={"latitude": 51.1, "longitude": 17.0})
        assert reply_events(s, NOW, cfg, []).startswith("Tue 20.10 18:00 – Wroc.NET #171")


def test_broken_site_does_not_stop_the_others(run_events):
    sent = run_events({"meetup": ValueError("layout changed"), "crossweb": [event()]})
    assert sent[0] == "⚠️ Events: meetup failed: ValueError: layout changed"
    assert sent[1].startswith("📅 1 new event\n\n")
    with pytest.raises(RuntimeError):
        run_events({"meetup": ValueError("x"), "crossweb": ValueError("y")})


def test_alert_fits_one_telegram_message():
    many = [event(id=f"x:{i}", title=f"Wroc.NET #{i}", url="https://crossweb.pl/" + "x" * 80)
            for i in range(80)]  # fmt: skip
    text = collector.alert_text(many, TZ)
    assert len(text) <= collector.TELEGRAM_MAX
    assert text.startswith("📅 80 new events") and text.endswith("more in /events")
