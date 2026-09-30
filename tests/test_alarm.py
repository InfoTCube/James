from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import AlarmConfig, Config
from assistant.core.db import make_engine
from assistant.modules.alarm import collector, service
from assistant.modules.alarm.models import Alarm
from assistant.modules.mpk.service import Departure, Leg, Target
from assistant.services.bot.replies import reply_alarm, reply_alarm_off, reply_alarms

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})


def at(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


def departure(leave_by: datetime) -> Departure:
    home, work = Target("home", ["A"], 5), Target("work", ["B"], 5)
    leg = Leg("Praca", home, work, None, leave_by + timedelta(minutes=20), leave_by, leave_by)
    return Departure(leg, leave_by, None)


@pytest.fixture
def db(tmp_path):
    return make_engine(tmp_path / "db.sqlite")


def test_auto_alarm_40_min_before_leaving_only_before_11():
    cfg = AlarmConfig()
    assert service.auto_alarm_time(departure(at(7, 7, 40)), cfg, TZ) == at(7, 7, 0)
    assert service.auto_alarm_time(departure(at(5, 10, 58)), cfg, TZ) == at(5, 10, 18)
    assert service.auto_alarm_time(departure(at(2, 11, 40)), cfg, TZ) is None  # 11:00: up already
    assert service.auto_alarm_time(None, cfg, TZ) is None


def test_plan_auto_alarm_follows_calendar_but_respects_cancel(db, monkeypatch):
    dep = {"d": departure(at(7, 7, 40))}
    monkeypatch.setattr(service.mpk, "first_departure", lambda s, day, c: dep["d"])
    day = date(2026, 10, 7)

    def plan():
        with Session(db) as s, s.begin():
            service.plan_auto_alarm(s, day, CONFIG)
        with Session(db) as s:
            return [(a.at, a.cancelled) for a in s.query(Alarm)]

    assert plan() == [(at(7, 7, 0), False)]
    dep["d"] = departure(at(7, 8, 10))  # event moved later
    assert plan() == [(at(7, 7, 30), False)]
    dep["d"] = None  # event deleted
    assert plan() == []
    dep["d"] = departure(at(7, 7, 40))
    plan()
    with Session(db) as s, s.begin():
        service.cancel_alarm(s, s.query(Alarm).one().id)
    dep["d"] = departure(at(7, 8, 10))
    assert plan() == [(at(7, 7, 0), True)]  # cancelled stays cancelled


def test_parse_time():
    now = at(7, 12, 0)
    assert service.parse_time("7:30", now, TZ) == at(8, 7, 30)  # already past → tomorrow
    assert service.parse_time("13.05", now, TZ) == at(7, 13, 5)
    assert service.parse_time("1930", now, TZ) == at(7, 19, 30)
    assert service.parse_time("21", now, TZ) == at(7, 21, 0)
    assert service.parse_time("25:00", now, TZ) is None
    assert service.parse_time("soon", now, TZ) is None


def test_bot_alarm_commands(db):
    now = at(7, 12, 0)
    with Session(db) as s, s.begin():
        assert reply_alarm(s, now, CONFIG, ["7:30", "gym"]) == "⏰ Alarm #1 set for Thu 08.10 07:30"
        assert reply_alarm(s, now, CONFIG, []) == "Usage: /alarm 7:30 [label]"
    with Session(db) as s:
        assert reply_alarms(s, now, CONFIG, []) == "#1 Thu 08.10 07:30 · gym"
    with Session(db) as s, s.begin():
        assert reply_alarm_off(s, now, CONFIG, ["#1"]) == "Alarm #1 cancelled."
        assert reply_alarm_off(s, now, CONFIG, ["1"]) == "No such upcoming alarm."
    with Session(db) as s:
        assert reply_alarms(s, now, CONFIG, []) == "No alarms set."


@pytest.fixture
def clock(db, monkeypatch):
    """Fake time + captured Telegram messages for the ringer, actions and routes."""
    from assistant.modules.alarm import actions, routes

    state = {"t": at(8, 7, 30), "sent": []}
    for mod in (collector, routes):
        monkeypatch.setattr(mod, "utcnow", lambda: state["t"])
        monkeypatch.setattr(mod, "load_config", lambda: CONFIG)
    monkeypatch.setattr(routes, "get_engine", lambda: db)
    monkeypatch.setattr(service, "briefing", lambda s, n, c: "BRIEFING")
    monkeypatch.setattr(
        actions, "notify", lambda s, text, key=None, buttons=None: state["sent"].append(text)
    )
    return state


def tick(db, clock, t):
    clock["t"] = t
    with Session(db) as s, s.begin():
        collector.AlarmRinger().run(s)


def test_ring_then_stop_starts_the_briefing(db, clock):
    from assistant.modules.alarm import routes

    with Session(db) as s, s.begin():
        service.add_alarm(s, at(8, 7, 30), "gym")
        service.add_alarm(s, at(8, 6, 0))  # long past: never rings
    tick(db, clock, at(8, 7, 29))
    assert routes.alarm_state() == {"state": "idle"}
    tick(db, clock, at(8, 7, 30))
    assert clock["sent"] == ["⏰ 07:30 · gym"]
    assert routes.alarm_state()["state"] == "ringing"
    assert routes.stop_alarm(1) == {"state": "briefing", "id": 1}
    assert clock["sent"][-1] == "BRIEFING"
    with pytest.raises(routes.HTTPException):
        routes.snooze_alarm(1)  # not ringing any more
    clock["t"] = at(8, 7, 34)
    assert routes.alarm_state() == {"state": "idle"}  # briefing shown for 3 min


def test_snooze_rings_again_and_timeout_starts_briefing(db, clock):
    from assistant.modules.alarm import routes

    with Session(db) as s, s.begin():
        service.add_alarm(s, at(8, 7, 30))
    tick(db, clock, at(8, 7, 30))
    clock["t"] = at(8, 7, 31)
    assert routes.snooze_alarm(1) == {"state": "idle"}
    assert clock["sent"][-1] == "😴 Snoozed until 07:41"
    tick(db, clock, at(8, 7, 40))
    assert routes.alarm_state() == {"state": "idle"}
    tick(db, clock, at(8, 7, 41))  # rings again
    assert routes.alarm_state()["seconds_left"] == 180
    tick(db, clock, at(8, 7, 43))
    assert clock["sent"][-1] != "BRIEFING"
    tick(db, clock, at(8, 7, 44))  # 3 min without an answer → stopped, briefing
    assert clock["sent"][-1] == "BRIEFING"
    assert routes.alarm_state() == {"state": "briefing", "id": 1}


def test_planner_leaves_a_snoozed_alarm_alone(db, monkeypatch):
    monkeypatch.setattr(service.mpk, "first_departure", lambda s, day, c: departure(at(7, 7, 40)))
    with Session(db) as s, s.begin():
        alarm = service.plan_auto_alarm(s, date(2026, 10, 7), CONFIG)
        alarm.rung_at = at(7, 7, 0)
        service.snooze(alarm, at(7, 7, 1))
    with Session(db) as s, s.begin():
        assert service.plan_auto_alarm(s, date(2026, 10, 7), CONFIG).at == at(7, 7, 11)


def test_briefing_with_empty_db(db):
    with Session(db) as s:
        text = service.briefing(s, at(7, 7, 0), CONFIG)
    assert text.splitlines() == [
        "⏰ Good morning! It's 07:00.",
        "📅 Nothing in the calendar today.",
    ]


def test_next_alarm_widget(db, clock):
    from assistant.modules.alarm import routes

    clock["t"] = at(7, 22, 0)
    assert routes.next_alarm_widget().body == b""
    with Session(db) as s, s.begin():
        service.add_alarm(s, at(8, 7, 0))
        service.add_alarm(s, at(10, 9, 30))
    assert routes.next_alarm_widget().body.decode() == "⏰ 07:00 <span>tomorrow</span>"
    clock["t"] = at(8, 7, 5)  # the 07:00 one rang already → the next one
    with Session(db) as s, s.begin():
        s.get(Alarm, 1).rung_at = at(8, 7, 0)
    assert routes.next_alarm_widget().body.decode() == "⏰ 09:30 <span>Sat</span>"
