import io
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Place
from assistant.core.db import make_engine
from assistant.modules.calendar.service import Event
from assistant.modules.mpk import collector as mpk_collector
from assistant.modules.mpk import service
from assistant.modules.mpk.collector import effective_date, import_feed, pick_current
from assistant.modules.mpk.models import MpkGeocode, MpkServiceDate

TZ = ZoneInfo("Europe/Warsaw")
FIXTURE = Path("tests/fixtures/mpk/gtfs")
PLACES = {
    "home": Place(stops=["DWORZEC GŁÓWNY", "Kościuszki"], keywords=["Kościuszki 71"]),
    "uni": Place(stops=["PL. GRUNWALDZKI", "most Grunwaldzki"], keywords=["Politechnika"]),
}
HOME = service.Target("home", PLACES["home"].stops, 5)
UNI = service.Target("uni", PLACES["uni"].stops, 5)


def at(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


def test_pick_current_ignores_future_and_undated_files():
    files = {
        139: "OtwartyWroclaw_rozklad_jazdy_GTFS_26092026.zip",
        138: "OtwartyWroclaw_rozklad_jazdy_GTFS_16092026.zip",
        140: "OtwartyWroclaw_rozklad_jazdy_GTFS_10102026.zip",  # published early
        141: "",
    }
    assert effective_date(files[139]) == date(2026, 9, 26)
    assert pick_current(files, date(2026, 9, 30)) == 139
    assert pick_current(files, date(2026, 10, 10)) == 140
    assert pick_current(files, date(2026, 9, 1)) is None


@pytest.fixture
def db(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in FIXTURE.iterdir():
            zf.write(f, f.name)
    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        import_feed(s, zipfile.ZipFile(buf))
    return engine


def test_service_dates_apply_exceptions(db):
    with Session(db) as s:

        def days(service_id):
            return set(
                s.scalars(select(MpkServiceDate.day).where(MpkServiceDate.service_id == service_id))
            )

        assert date(2026, 10, 1) in days("6") and date(2026, 10, 3) not in days("6")
        assert date(2026, 10, 8) not in days("6") and date(2026, 10, 8) in days("3")


def test_connections_board_late_get_off_early(db):
    with Session(db) as s:
        cs = service.connections(s, HOME, UNI, arrive_by=at(1, 9, 55), not_before=at(1, 9), tz=TZ)
    got = [(c.line, c.from_stop, c.departs, c.to_stop, c.arrives, c.is_tram) for c in cs]
    assert got == [
        ("145", "DWORZEC GŁÓWNY", at(1, 9, 35), "PL. GRUNWALDZKI", at(1, 9, 47), False),
        ("16", "Kościuszki", at(1, 9, 33), "most Grunwaldzki", at(1, 9, 40), True),
    ]  # t16b arrives 10:00, too late


def test_connections_respect_not_before_and_service_day(db):
    with Session(db) as s:
        late = service.connections(s, HOME, UNI, at(1, 9, 55), not_before=at(1, 9, 34), tz=TZ)
        assert [c.line for c in late] == ["145"]
        holiday = service.connections(s, HOME, UNI, at(8, 9, 55), not_before=at(8, 9), tz=TZ)
        assert [c.line for c in holiday] == ["145"]  # 8 Oct runs the Saturday service only


def test_resolve(db):
    with Session(db) as s:
        names = [*service.stop_names(s), "Rynek"]
    for text, label in [
        ("uni", "uni"),
        ("Politechnika Wrocławska, C-13", "uni"),
        ("ul. Kościuszki 71", "home"),
        ("Pl. Grunwaldzki 1", "uni"),
        ("Faculty of Environmental Engineering, Plac Grunwaldzki 13, Wrocław", "uni"),
        ("Rynek 5", "Rynek"),  # stop outside any place
        ("Rynk", "Rynek"),  # typo
        ("most grunwaldzki", "uni"),  # a stop that belongs to a place → the place
        ("Dworzec Glowny", "home"),
        ("Legnicka 5", None),
    ]:
        target = service.resolve(text, PLACES, names)
        assert (target.label if target else None) == label, text


WORK = service.Target("work", ["Rynek"], 5)
VB = service.Target("vb", ["Biegasa"], 5)


def test_plan_wednesday_work_home_then_lab():
    # work 8-16, lab 18:55-20:30: gap > 1.5 h → home in between
    legs = service.plan_legs(
        [("Praca", at(7, 8), at(7, 16), WORK), ("IOS", at(7, 18, 55), at(7, 20, 30), UNI)], HOME
    )
    got = [(g.title, g.origin.label, g.dest.label, g.show_from, g.show_until) for g in legs]
    assert got == [
        ("Praca", "home", "work", at(7, 6), at(7, 8)),
        ("Home", "work", "home", at(7, 15, 30), at(7, 17)),
        ("IOS", "home", "uni", at(7, 16, 55), at(7, 18, 55)),
        ("Home", "uni", "home", at(7, 20), at(7, 21, 30)),
    ]
    assert legs[1].arrive_by is None and legs[1].not_before == at(7, 16)


def test_plan_goes_straight_on_and_skips_same_place():
    # classes back to back at uni, then volleyball 1 h after → uni → vb directly
    legs = service.plan_legs(
        [
            ("Class 1", at(5, 10), at(5, 12), UNI),
            ("Class 2", at(5, 12, 15), at(5, 14), UNI),
            ("Volleyball", at(5, 15), at(5, 17), VB),
        ],
        HOME,
    )
    got = [(g.title, g.origin.label, g.dest.label) for g in legs]
    assert got == [("Class 1", "home", "uni"), ("Volleyball", "uni", "vb"), ("Home", "vb", "home")]
    straight = legs[1]
    assert straight.not_before == at(5, 14) and straight.show_from == at(5, 13, 30)


def test_plan_unknown_location():
    legs = service.plan_legs([("Party", at(5, 20), at(5, 23), None)], HOME)
    assert [(g.title, g.origin and g.origin.label, g.dest and g.dest.label) for g in legs] == [
        ("Party", "home", None),
        ("Home", None, "home"),
    ]


def test_connections_earliest_first_for_going_home(db):
    with Session(db) as s:
        cs = service.connections(
            s, HOME, UNI, arrive_by=at(1, 11), not_before=at(1, 9), tz=TZ, latest_first=False
        )
    assert [(c.line, c.departs) for c in cs] == [
        ("16", at(1, 9, 33)),
        ("145", at(1, 9, 35)),
        ("16", at(1, 9, 53)),
    ]


def test_resolve_near_and_cached_lookup(db):
    with Session(db) as s, s.begin():
        s.add(
            MpkGeocode(query="Klub X, Legnicka 5", lat=51.1041, lon=17.0345, looked_up_at=at(1, 0))
        )
        s.add(MpkGeocode(query="Nowhere 1", lat=None, lon=None, looked_up_at=at(1, 0)))
        s.add(MpkGeocode(query="Far away 2", lat=51.2, lon=16.9, looked_up_at=at(1, 0)))
    with Session(db) as s:
        # ~50 m from the Kościuszki stop, which belongs to home
        assert service.find_target(s, "Klub X, Legnicka 5", PLACES).label == "home"
        # no place nearby → nearest stops, labelled with the first part of the location
        target = service.find_target(s, "Klub X, Legnicka 5", {})
        assert (target.label, target.stops[0], target.walk_minutes) == ("Klub X", "Kościuszki", 1)
        assert service.find_target(s, "Nowhere 1", PLACES) is None
        assert service.find_target(s, "Far away 2", PLACES) is None  # no stop within 400 m
        assert service.find_target(s, "Not looked up yet", PLACES) is None


def test_geocode_collector_looks_up_only_unmatched_new_locations(db, monkeypatch):
    events = [
        Event("Class", at(5, 10), at(5, 12), "Politechnika"),  # matched by text
        Event("Party", at(5, 20), at(5, 23), "Klub X, Legnicka 5"),
        Event("Party again", at(6, 20), at(6, 23), "Klub X, Legnicka 5"),  # same text
        Event("Birthday", at(6, 0), at(7, 0), "Somewhere", all_day=True),  # all-day: skipped
    ]
    asked = []
    monkeypatch.setattr(mpk_collector.calendar, "events_between", lambda *a, **k: events)
    monkeypatch.setattr(mpk_collector, "load_config", lambda: SimpleNamespace(places=PLACES, tz=TZ))
    monkeypatch.setattr(mpk_collector, "geocode", lambda client, q: asked.append(q) or (51.1, 17.0))
    for _ in range(2):  # second run: already cached
        with Session(db) as s, s.begin():
            mpk_collector.GeocodeCollector().run(s)
    assert asked == ["Klub X, Legnicka 5"]


def test_after_timetable_ends_weekday_services_are_reused(db):
    with Session(db) as s:
        assert service.timetable_end(s) == date(2026, 10, 10)  # last day with any service
        assert service.services_on(s, date(2026, 10, 8)) == ["3"]  # the holiday itself
        assert service.services_on(s, date(2026, 10, 15)) == ["6"]  # Thursday after the end
        assert service.services_on(s, date(2026, 10, 17)) == ["3"]  # Saturday after the end
        cs = service.connections(s, HOME, UNI, at(15, 9, 55), not_before=at(15, 9), tz=TZ)
    assert [(c.line, c.departs) for c in cs] == [("145", at(15, 9, 35)), ("16", at(15, 9, 33))]


def test_parse_positions_swaps_x_y():
    raw = json.loads(Path("tests/fixtures/mpk/bus_position.json").read_text(encoding="utf-8"))
    v = service.parse_positions(raw)[0]
    assert (v.course, v.line, v.lat, v.lon) == (29106483, "145", 51.10648, 17.103416)


# trip t16a heading north: DWORZEC GŁÓWNY 09:30 → Kościuszki 09:33 (your boarding stop)
T16A_TO_KOSCIUSZKI = [(51.098, 17.036, 9 * 3600 + 30 * 60), (51.104, 17.034, 9 * 3600 + 33 * 60)]
MIDNIGHT = at(1, 0)


def tram(lat: float, course: int = 1) -> service.Vehicle:
    return service.Vehicle(course, "16", lat, 17.036)


def delay_after(positions: list[float], now: datetime) -> int | None:
    tracker = service.Tracker()
    for i, lat in enumerate(positions):
        tracker.update([tram(lat)], now - timedelta(minutes=len(positions) - 1 - i))
    return service.estimate_delay(T16A_TO_KOSCIUSZKI, MIDNIGHT, [tram(positions[-1])], tracker, now)


def test_live_delay_needs_movement_towards_your_stop():
    # at DWORZEC GŁÓWNY at 09:32, having come from the south: 2 min late
    assert delay_after([51.094, 51.098], at(1, 9, 32)) == 2
    assert delay_after([51.094, 51.098], at(1, 9, 29)) == -1  # a bit early
    assert delay_after([51.098], at(1, 9, 32)) is None  # first sighting: direction unknown
    assert delay_after([51.1005, 51.098], at(1, 9, 32)) is None  # moving away: other direction
    assert delay_after([51.098, 51.0982], at(1, 9, 32)) is None  # GPS jitter, not a move
    assert delay_after([51.094, 51.098], at(1, 9, 55)) is None  # 25 min "late": another trip
    assert delay_after([51.080, 51.084], at(1, 9, 32)) is None  # not near any stop of the trip


def test_live_delay_picks_the_most_plausible_vehicle():
    # two line-16 trams heading north: one reached Kościuszki (09:33 → 3 min late),
    # the next one is at DWORZEC GŁÓWNY (09:30 → 6 min late); the smaller delay is ours
    now, tracker = at(1, 9, 36), service.Tracker()
    tracker.update([tram(51.099, course=1), tram(51.090, course=2)], now - timedelta(minutes=1))
    moved = [tram(51.104, course=1), tram(51.098, course=2)]
    tracker.update(moved, now)
    assert service.estimate_delay(T16A_TO_KOSCIUSZKI, MIDNIGHT, moved, tracker, now) == 3


def test_live_delay_interpolates_between_stops():
    # halfway between DWORZEC GŁÓWNY (09:30) and Kościuszki (09:33) → scheduled 09:31:30
    now = at(1, 9, 33) + timedelta(seconds=30)
    assert delay_after([51.097, 51.101], now) == 2


def test_late_trip_shows_connections_arriving_up_to_20_min_late(db, monkeypatch):
    from assistant.core.config import Config

    config = Config(location={"latitude": 51.1, "longitude": 17.0}, places=PLACES)
    leg = service.Leg("Class", HOME, UNI, None, at(1, 9, 45), at(1, 7, 45), at(1, 9, 45))
    monkeypatch.setattr(service, "day_legs", lambda s, n, c: [leg])
    with Session(db) as s:
        on_time = service.current_trip(s, at(1, 9, 20), config)
        late = service.current_trip(s, at(1, 9, 36), config)
    assert on_time.late_min is None and on_time.options
    # nothing leaves after 09:41 that arrives by 09:40; tram 16 at 09:53 arrives 10:00 (+5 walk)
    assert late.late_min == 20
    assert [(c.line, c.departs, c.arrives) for c in late.options] == [
        ("16", at(1, 9, 53), at(1, 10, 0))
    ]
    assert late.leave_by == at(1, 9, 48)
