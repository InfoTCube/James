import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.flights import collector, service
from assistant.modules.flights.models import FlightFare, FlightWatch
from assistant.services.bot.replies import (
    reply_flight_add,
    reply_flight_del,
    reply_flight_price,
    reply_flights,
)

FIXTURES = Path(__file__).parent / "fixtures" / "flights"
NOW = datetime(2026, 10, 6, 10, tzinfo=UTC)
TODAY = date(2026, 10, 6)
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})


def load(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text if name.endswith(".html") else json.loads(text)


def watch(**kw) -> FlightWatch:
    base = dict(destinations="", first_day=date(2026, 11, 1), last_day=date(2026, 11, 30),
                nights_min=2, nights_max=4, max_price=300)  # fmt: skip
    return FlightWatch(**base | kw)


def fare(**kw) -> FlightFare:
    base = dict(airline="Ryanair", destination="GDN", city="Gdansk", out_day=date(2026, 11, 9),
                back_day=date(2026, 11, 13), price=131.0, url="u")  # fmt: skip
    return FlightFare(**base | kw)


def test_parse_watch():
    w = service.parse_watch(["any", "1.11-30.11", "2-4", "300"], TODAY)
    assert (w.destinations, w.first_day, w.last_day) == ("", date(2026, 11, 1), date(2026, 11, 30))
    assert (w.nights_min, w.nights_max, w.max_price) == (2, 4, 300)
    w = service.parse_watch(["bcn,mad", "15.12-6.01", "7", "600zł"], TODAY)
    assert (w.destinations, w.last_day, w.nights_min, w.nights_max) == (
        "BCN,MAD", date(2027, 1, 6), 7, 7
    )  # fmt: skip
    w = service.parse_watch(["LTN", "1.10", "oneway", "150"], TODAY)  # passed: next year's
    assert (w.first_day, w.last_day, w.nights_min) == (date(2027, 10, 1), date(2027, 10, 1), None)
    for bad in (
        ["any", "1.11-30.11", "2-4"],
        ["Barcelona", "1.11", "oneway", "1"],
        ["any", "32.11", "3", "1"],
        ["any", "1.11-3.11", "4", "1"],
        ["any", "1.11-30.11", "4-2", "1"],
        ["any", "1.11-30.11", "3", "cheap"],
    ):
        with pytest.raises(ValueError):
            service.parse_watch(bad, TODAY)


def test_describe():
    assert service.describe(watch(id=3)) == "#3 anywhere · 01.11–30.11 · 2–4 nights · ≤ 300 zł"
    one = watch(id=4, destinations="LTN,STN", last_day=date(2026, 11, 1), nights_min=None)
    assert service.describe(one) == "#4 LTN, STN · 01.11 · one way · ≤ 300 zł"


def test_parse_ryanair():
    fares = collector.parse_ryanair(load("ryanair_return.json"))
    f = fares[0]
    assert (f.airline, f.destination, f.city, f.price) == ("Ryanair", "GDN", "Gdansk", 131.31)
    assert (f.out_day, f.back_day) == (date(2026, 11, 9), date(2026, 11, 13))
    assert "dateOut=2026-11-09&dateIn=2026-11-13" in f.url and "destinationIata=GDN" in f.url
    one = collector.parse_ryanair(load("ryanair_oneway.json"))[0]
    assert one.back_day is None and "isReturn=false" in one.url


def test_parse_wizz():
    assert collector.wizz_api(load("wizz_home.html")) == "https://be.wizzair.com/29.19.0/Api"
    with pytest.raises(ValueError):
        collector.wizz_api("<html></html>")
    routes = collector.wizz_routes(load("wizz_map.json"))
    assert len(routes) == 28 and routes["LTN"] == "London Luton"
    tt = load("wizz_timetable.json")
    out = collector.wizz_prices(tt["outboundFlights"])
    assert date(2026, 11, 1) not in out  # "checkPrice": no price that day
    assert out[date(2026, 11, 16)] == 80.9


def test_wizz_best():
    tt = load("wizz_timetable.json")
    out, back = (
        collector.wizz_prices(tt["outboundFlights"]),
        collector.wizz_prices(tt["returnFlights"]),
    )
    f = collector.wizz_best("LTN", "London Luton", out, back, watch(), TODAY)
    # 80.9 out on 16.11, 149 back on 18.11 (2 nights) — or 20.11; first cheapest wins
    assert (f.price, f.out_day, f.back_day) == (229.9, date(2026, 11, 16), date(2026, 11, 18))
    assert f.url == ("https://www.wizzair.com/en-gb/booking/select-flight/WRO/LTN/2026-11-16/"
                     "2026-11-18/1/0/0/null")  # fmt: skip
    one = collector.wizz_best("LTN", "L", out, {}, watch(nights_min=None, nights_max=None), TODAY)
    assert (one.price, one.back_day) == (80.9, None)
    short = watch(first_day=date(2026, 11, 1), last_day=date(2026, 11, 3))
    assert collector.wizz_best("LTN", "L", out, back, short, TODAY) is None


def copy(f: FlightFare) -> FlightFare:
    """A fresh, unsaved fare, like each real fetch returns."""
    skip = ("watch_id", "alerted_price")
    return FlightFare(**{c.key: getattr(f, c.key) for c in FlightFare.__table__.c
                         if c.key not in skip})  # fmt: skip


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    sent = []
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text))
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)
    db = type("Db", (), {"engine": engine, "sent": sent})

    def run(now=NOW, **airlines):
        """Run the collector; airlines: {name: [fares] or an exception}, same for every watch."""
        monkeypatch.setattr(collector, "utcnow", lambda: now)

        def fetcher(result):
            def fetch(watches, today):
                if isinstance(result, Exception):
                    raise result
                return {w.id: [copy(f) for f in result] for w in watches}

            return fetch

        monkeypatch.setattr(collector, "SOURCES", {a: fetcher(r) for a, r in airlines.items()})
        with Session(engine) as s, s.begin():
            collector.FlightsCollector().run(s)

    db.run = run
    return db


def add(db, *args) -> str:
    with Session(db.engine) as s, s.begin():
        return reply_flight_add(s, NOW, CONFIG, list(args))


def test_alerts_deals_once_and_on_a_real_drop(db):
    assert add(db, "any", "1.11-30.11", "2-4", "300").startswith("✈️ Watching #1 anywhere")
    db.run(Ryanair=[fare(), fare(destination="BCN", city="Barcelona", price=420.0)])
    assert len(db.sent) == 1 and db.sent[0].startswith("✈️ 1 flight deal\n\n131 zł Gdansk (GDN)")
    db.run(Ryanair=[fare(price=125.0)])  # not due yet (checked < 12 h ago)
    later = NOW + timedelta(days=1)  # due again, in the daytime
    db.run(later, Ryanair=[fare(price=125.0)])  # 5% cheaper: not worth a message
    assert len(db.sent) == 1
    db.run(later + timedelta(days=1), Ryanair=[fare(price=110.0)])  # >10% cheaper
    assert len(db.sent) == 2 and "110 zł" in db.sent[1]
    with Session(db.engine) as s:
        assert [(f.destination, f.price) for f in service.fares(s, 1)] == [("GDN", 110.0)]


def test_raising_the_price_sends_what_is_now_under_it(db):
    add(db, "any", "1.11-30.11", "2-4", "300")
    db.run(Ryanair=[fare(destination="BCN", city="Barcelona", price=420.0)])
    assert db.sent == []
    with Session(db.engine) as s, s.begin():
        assert reply_flight_price(s, NOW, CONFIG, ["1", "450"]).startswith("✈️ Updated #1")
    db.run(Ryanair=[fare(destination="BCN", city="Barcelona", price=420.0)])  # due again at once
    assert "420 zł Barcelona" in db.sent[0]


def test_destinations_filter_and_failing_airline(db):
    add(db, "LTN", "1.11-30.11", "2-4", "300")
    db.run(
        Ryanair=ValueError("blocked"),
        **{
            "Wizz Air": [
                fare(airline="Wizz Air"),
                fare(airline="Wizz Air", destination="LTN", city="London Luton", price=229.9),
            ]
        },
    )
    assert db.sent[0] == "⚠️ Flights: Ryanair failed: ValueError: blocked"
    assert "London Luton (LTN) · Wizz Air" in db.sent[1] and "GDN" not in db.sent[1]
    with pytest.raises(RuntimeError):
        db.run(NOW + timedelta(days=1), Ryanair=ValueError("x"), **{"Wizz Air": ValueError("y")})


def test_watch_over_is_removed(db):
    add(db, "any", "1.11-30.11", "2-4", "300")
    db.run(Ryanair=[])
    db.run(datetime(2026, 11, 29, 10, tzinfo=UTC), Ryanair=[])  # 30.11 - 2 nights: too late
    assert db.sent == ["✈️ Watch over, removed: #1 anywhere · 01.11–30.11 · 2–4 nights · ≤ 300 zł"]


def test_bot_replies(db):
    assert "Usage" in add(db, "any", "1.11")
    add(db, "any", "1.11-30.11", "2-4", "300")
    db.run(Ryanair=[fare(), fare(destination="BCN", city="Barcelona", price=420.0)])
    with Session(db.engine) as s, s.begin():
        text = reply_flights(s, NOW, CONFIG, [])
        assert "✅ 131 zł Gdansk (GDN)" in text and "· 420 zł Barcelona (BCN)" in text
        assert [f.destination for _, f in service.deals(s)] == ["GDN"]
        assert reply_flight_del(s, NOW, CONFIG, ["#1"]).startswith("🗑️ Deleted #1")
        assert reply_flight_del(s, NOW, CONFIG, ["1"]) == "No such watch."
    with Session(db.engine) as s:
        assert service.fares(s, 1) == []


def test_alert_fits_one_telegram_message():
    w = watch(id=1)
    deals = [(w, fare(destination=f"X{i:02}", url="https://www.ryanair.com/" + "x" * 150))
             for i in range(60)]  # fmt: skip
    text = collector.alert_text(deals)
    assert len(text) <= collector.TELEGRAM_MAX
    assert text.startswith("✈️ 60 flight deals") and text.endswith("more in /flights")


def test_tap_sends_the_deal_to_telegram(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.flights import routes
    from assistant.services.api.app import app

    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        s.add(watch())
        s.flush()
        s.add(fare(watch_id=1, airline="Wizz Air", destination="LTN", city="London Luton"))
    sent = []
    monkeypatch.setattr(routes, "get_engine", lambda: engine)
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    client = TestClient(app)
    html = client.get("/widgets/flights").text
    assert "London Luton (LTN)" in html and 'hx-post="/api/flights/send/1/Wizz%20Air/LTN"' in html
    assert client.post("/api/flights/send/1/Wizz%20Air/LTN").text == "✓ sent to Telegram"
    assert sent[0].startswith("🔗 131 zł London Luton (LTN) · Wizz Air")
    assert client.post("/api/flights/send/1/Ryanair/LTN").text == "✗ gone"


def test_widget_without_deals_says_why(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.flights import routes
    from assistant.services.api.app import app

    engine = make_engine(tmp_path / "db.sqlite")
    monkeypatch.setattr(routes, "get_engine", lambda: engine)
    client = TestClient(app)
    assert "No flight watches" in client.get("/widgets/flights").text
    with Session(engine) as s, s.begin():
        s.add(watch())
        s.add(watch(checked_at=NOW))
        s.flush()
        s.add(fare(watch_id=2, price=420.0, city="Barcelona"))
    html = client.get("/widgets/flights").text
    assert "checking within 15 minutes" in html
    assert "nothing under the price; cheapest 420 zł Barcelona" in html


def test_no_checks_at_night(db):
    add(db, "any", "1.11-30.11", "2-4", "300")
    night = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)  # 23:30 in Warsaw
    db.run(night, Ryanair=[fare()])
    assert db.sent == []
    db.run(night + timedelta(hours=8), Ryanair=[fare()])  # 07:30: the waiting check runs
    assert db.sent[0].startswith("✈️ 1 flight deal")
