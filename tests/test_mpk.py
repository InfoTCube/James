import io
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Place
from assistant.core.db import make_engine
from assistant.modules.mpk import service
from assistant.modules.mpk.collector import effective_date, import_feed, pick_current
from assistant.modules.mpk.models import MpkServiceDate

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
