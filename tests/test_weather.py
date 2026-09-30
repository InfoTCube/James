import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.db import make_engine
from assistant.modules.weather.collector import parse
from assistant.modules.weather.models import WeatherHourly
from assistant.modules.weather.service import (
    clothing_advice,
    default_window,
    get_clothing_advice,
    outdoor_windows,
)

TZ = ZoneInfo("Europe/Warsaw")
FIXTURE = json.loads(Path("tests/fixtures/weather/forecast.json").read_text(encoding="utf-8"))


def hours(feels: list[float], start=datetime(2026, 10, 1, 6, tzinfo=UTC), **kw) -> list:
    return [
        WeatherHourly(
            time=start + timedelta(hours=i),
            temp=f,
            feels_like=f,
            precip_prob=kw.get("prob", 0),
            precip_mm=0.0,
            wind_kmh=kw.get("wind", 5.0),
            code=0,
        )
        for i, f in enumerate(feels)
    ]


def test_parse_fixture():
    rows = parse(FIXTURE)
    assert len(rows) == 48
    assert rows[0].time.tzinfo is UTC and rows[1].time - rows[0].time == timedelta(hours=1)
    assert isinstance(rows[0].temp, float) and isinstance(rows[0].code, int)


def test_parse_skips_hours_with_nulls():
    data = json.loads(json.dumps(FIXTURE))
    data["hourly"]["temperature_2m"][5] = None
    assert len(parse(data)) == 47


def test_rerun_upserts(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    for _ in range(2):
        with Session(engine) as s, s.begin():
            for row in parse(FIXTURE):
                s.merge(row)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(WeatherHourly)) == 48


def test_dress_for_the_coldest_hour_and_warn_about_drop():
    advice = clothing_advice(hours([18, 16, 14, 11, 9]), TZ)
    assert advice.layers == "Light jacket"
    assert advice.extras == ["Starts at 18°, drops to 9° by 12:00"]


def test_rain_wind_heat():
    assert clothing_advice(hours([25], prob=60), TZ).extras == ["Take an umbrella, rain from 08:00"]
    assert clothing_advice(hours([25], wind=45), TZ).extras == ["Very windy"]
    assert clothing_advice(hours([30]), TZ).extras == ["Hot, take water"]
    assert clothing_advice(hours([-3]), TZ).layers == "Winter jacket, hat and gloves"
    assert clothing_advice([], TZ) is None


def local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 1, h, m, tzinfo=TZ)


def test_outdoor_windows_short_break_is_ignored():
    classes = [(local(10), local(12)), (local(12, 15), local(14))]
    assert outdoor_windows(classes, now=local(7)) == [
        (local(9, 30), local(10)),
        (local(14), local(14, 30)),
    ]


def test_outdoor_windows_long_break_counts_and_overlaps_merge():
    events = [(local(15), local(17)), (local(8), local(10)), (local(9), local(11))]
    assert outdoor_windows(events, now=local(7)) == [
        (local(7, 30), local(8)),
        (local(11), local(15)),
        (local(17), local(17, 30)),
    ]


def test_outdoor_windows_drops_the_past():
    classes = [(local(10), local(12)), (local(12, 15), local(14))]
    assert outdoor_windows(classes, now=local(14, 10)) == [(local(14, 10), local(14, 30))]
    assert outdoor_windows(classes, now=local(15)) == []


def test_default_window_until_22_local_and_looks_ahead_late_at_night():
    assert default_window(local(8, 30), TZ) == (local(8, 30), local(22))
    start, end = default_window(local(23, 10), TZ)
    assert end - start == timedelta(hours=3)


def test_get_clothing_advice_reads_db(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        s.add_all(hours([5] * 20))
    with Session(engine) as s:
        advice = get_clothing_advice(s, datetime(2026, 10, 1, 7, tzinfo=UTC), TZ)
    assert advice.layers == "Warm jacket"


def test_advice_uses_only_outdoor_hours(tmp_path):
    # cold morning, freezing midday (you're in class), mild afternoon
    engine = make_engine(tmp_path / "db.sqlite")
    feels = {7: 6, 8: 6, 9: 6, 10: -5, 11: -5, 12: -5, 13: -5, 14: 15, 15: 15}
    with Session(engine) as s, s.begin():
        for h, f in feels.items():
            s.add(hours([f], start=local(h))[0])
    classes = [(local(10), local(12)), (local(12, 15), local(14))]
    with Session(engine) as s:
        advice = get_clothing_advice(s, local(7), TZ, events=classes)
    assert advice.layers == "Warm jacket"  # 09:00 hour at 6°, not the -5° while inside
