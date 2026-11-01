import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.sports import collector, service
from assistant.modules.sports.models import SportsMatch, SportsTeam
from assistant.services.bot.replies import reply_sports
from assistant.services.voice import intents

FIXTURES = Path(__file__).parent / "fixtures" / "sports"
TZ = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
SLASK, TREFL = "133952", "152049"
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0}, sports={"teams": ["Slask Wroclaw"]})


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_find_team_takes_football_or_volleyball():
    assert collector.find_team(load("team_slask")) == (SLASK, "Śląsk Wrocław")
    assert collector.find_team({"teams": None}) is None
    assert collector.find_team({"teams": [{"strSport": "Handball", "idTeam": "1"}]}) is None


def test_parse_upcoming_and_finished():
    [e] = load(f"eventsnext_{SLASK}")["events"]
    m = collector.parse_match(e, "x")
    assert (m.home, m.away, m.league) == ("Radomiak Radom", "Śląsk Wrocław", "Polish Ekstraklasa")
    assert m.starts == datetime(2026, 11, 2, 17, tzinfo=UTC) and m.home_score is None
    [e] = load(f"eventslast_{TREFL}")["results"]
    m = collector.parse_match(e, "x")
    assert (m.sport, m.home_score, m.away_score) == ("Volleyball", 1, 3)


def test_parse_skips_postponed_and_undated():
    [e] = load(f"eventsnext_{SLASK}")["events"]
    assert collector.parse_match({**e, "strPostponed": "yes"}, "x") is None
    assert collector.parse_match({**e, "strTimestamp": None}, "x") is None


def test_format_in_local_time():
    [e] = load(f"eventsnext_{SLASK}")["events"]
    assert service.format_match(collector.parse_match(e, "x"), TZ) == (
        "⚽ Mon 02.11 18:00 Radomiak Radom vs Śląsk Wrocław · Polish Ekstraklasa"
    )
    [e] = load(f"eventslast_{TREFL}")["results"]
    assert service.format_match(collector.parse_match(e, "x"), TZ) == (
        "🏐 17.03 Trefl Gdańsk 1:3 Bogdanka LUK Lublin · Polish PlusLiga"
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    pages = {
        "searchteams.php": lambda p: load("team_slask"),
        "eventsnext.php": lambda p: load(f"eventsnext_{p['id']}"),
        "eventslast.php": lambda p: load(f"eventslast_{p['id']}"),
    }
    calls, sent = [], []

    def get(url, params=None, **kw):
        page = url.rsplit("/", 1)[-1]
        calls.append(page)
        response = pages[page](params)
        if isinstance(response, Exception):
            raise response
        return httpx.Response(200, json=response, request=httpx.Request("GET", url))

    monkeypatch.setattr(collector.httpx, "get", get)
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text))
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)

    class Env:
        def __init__(self):
            self.engine, self.pages, self.calls, self.sent = engine, pages, calls, sent

        def run(self, now=NOW, config=CONFIG):
            monkeypatch.setattr(collector, "utcnow", lambda: now)
            monkeypatch.setattr(collector, "load_config", lambda: config)
            with Session(engine) as s, s.begin():
                collector.SportsCollector().run(s)

    return Env()


def test_run_stores_matches_and_searches_only_once(env):
    env.run()
    assert env.calls == ["searchteams.php", "eventsnext.php", "eventslast.php"]
    env.calls.clear()
    env.run()  # idempotent, team id remembered
    assert env.calls == ["eventsnext.php", "eventslast.php"]
    with Session(env.engine) as s:
        assert len(s.scalars(select(SportsMatch)).all()) == 2
        [next_match] = service.upcoming(s, NOW)
        [score] = service.results(s, NOW)
        assert next_match.home == "Radomiak Radom"
        assert (score.home_score, score.away_score) == (2, 0)


def test_a_played_match_gets_its_score(env):
    env.run()
    [e] = load(f"eventsnext_{SLASK}")["events"]
    played = {**e, "intHomeScore": "1", "intAwayScore": "1", "strStatus": "FT"}
    env.pages["eventslast.php"] = lambda p: {"results": [played]}
    env.pages["eventsnext.php"] = lambda p: {"events": None}
    env.run(NOW + timedelta(days=2))
    with Session(env.engine) as s:
        assert service.upcoming(s, NOW + timedelta(days=2)) == []
        assert service.results(s, NOW + timedelta(days=2))[0].home_score == 1


def test_unknown_team_warns_and_failure_of_all_raises(env):
    env.pages["searchteams.php"] = lambda p: {"teams": None}
    env.run()
    assert env.sent == ["⚠️ Sports: no team called Slask Wroclaw. Check sports.teams in config.yaml"]
    env.pages["searchteams.php"] = lambda p: httpx.ConnectError("down")
    with pytest.raises(RuntimeError):
        env.run()


def test_old_and_removed_matches_go(env):
    env.run()
    env.run(NOW + timedelta(days=40))  # refetched ones are old too by then
    with Session(env.engine) as s:
        assert s.scalars(select(SportsMatch)).all() == []
    env.run()
    off = Config(location={"latitude": 51.1, "longitude": 17.0})
    env.calls.clear()
    env.run(config=off)
    assert env.calls == []
    with Session(env.engine) as s:
        assert s.scalars(select(SportsMatch)).all() == []
        assert reply_sports(s, NOW, off, []).startswith("Sports is off")


PLUSLIGA = Config(
    location={"latitude": 51.1, "longitude": 17.0}, sports={"leagues": {"PlusLiga": 5619}}
)
BOGDANKA = Config(
    location={"latitude": 51.1, "longitude": 17.0},
    sports={"teams": ["Bogdanka"], "leagues": {"PlusLiga": 5619}},
)


def plusliga_day(p: dict) -> dict:
    days = {"2026-11-04": "day_5619", "2026-10-29": "day_5619_scores"}
    return load(days[p["d"]]) if p["d"] in days else {"events": None}


def follow_bogdanka(env) -> None:
    with Session(env.engine) as s, s.begin():
        s.add(SportsTeam(name="Bogdanka", team_id="152041", title="Bogdanka LUK Lublin"))


def test_league_asks_for_each_day_around_today(env):
    days = []
    env.pages["eventsday.php"] = lambda p: days.append(p["d"]) or plusliga_day(p)
    env.run(config=PLUSLIGA)
    assert (days[0], days[-1], len(days)) == ("2026-10-28", "2026-11-06", 10)
    env.run(config=PLUSLIGA)  # idempotent
    with Session(env.engine) as s:
        upcoming, results = service.upcoming(s, NOW), service.results(s, NOW)
        assert len(upcoming) == 3 and len(results) == 3
        assert upcoming[0].league == "Polish PlusLiga" and upcoming[0].team == "PlusLiga"


def test_a_failing_league_keeps_the_rest_and_only_all_failing_raises(env):
    def day(p):
        return httpx.ConnectError("down") if p["l"] == 1 else plusliga_day(p)

    env.pages["eventsday.php"] = day
    both = Config(
        location={"latitude": 51.1, "longitude": 17.0}, sports={"leagues": {"A": 1, "B": 2}}
    )
    env.run(config=both)
    with Session(env.engine) as s:
        assert {m.team for m in s.scalars(select(SportsMatch))} == {"B"}
    only_a = Config(location={"latitude": 51.1, "longitude": 17.0}, sports={"leagues": {"A": 1}})
    with pytest.raises(RuntimeError):
        env.run(config=only_a)


def test_rate_limit_is_waited_out_once(monkeypatch):
    statuses, waits = [429, 200], []

    def fake_get(url, **kw):
        return httpx.Response(
            statuses.pop(0), json={"events": []}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(collector.httpx, "get", fake_get)
    monkeypatch.setattr(collector.time, "sleep", waits.append)
    assert collector.get("eventsnext.php", id="1") == {"events": []}
    assert waits == [collector.PAUSE, collector.RETRY_AFTER]
    statuses[:] = [429, 429]
    with pytest.raises(httpx.HTTPStatusError):
        collector.get("eventsnext.php", id="1")


def test_teams_are_fetched_first_and_a_rate_limited_league_warns(env):
    def limited(p):
        return httpx.ConnectError("429")

    env.pages["eventsday.php"] = limited
    both = Config(
        location={"latitude": 51.1, "longitude": 17.0},
        sports={"teams": ["Slask Wroclaw"], "leagues": {"PlusLiga": 5619}},
    )
    env.run(config=both)
    assert env.calls[:3] == ["searchteams.php", "eventsnext.php", "eventslast.php"]
    assert env.sent == ["⚠️ Sports: PlusLiga failed: ConnectError: 429"]
    with Session(env.engine) as s:
        assert len(s.scalars(select(SportsMatch)).all()) == 2  # the team's matches are in


def test_voice_reads_only_your_teams(env):
    env.pages["eventsday.php"] = plusliga_day
    env.run(config=PLUSLIGA)
    follow_bogdanka(env)
    with Session(env.engine) as s:
        spoken = intents._sports(s, NOW, BOGDANKA, None)
        assert "Bogdanka LUK Lublin 3:0 GKS Katowice" in spoken and "Skra" not in spoken
        assert intents._sports(s, NOW, PLUSLIGA, None).startswith("No matches of your teams")


def test_widget_marks_your_teams(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.sports import routes
    from assistant.services.api.app import app

    env.pages["eventsday.php"] = plusliga_day
    env.run(config=PLUSLIGA)
    follow_bogdanka(env)
    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    monkeypatch.setattr(routes, "utcnow", lambda: NOW)
    monkeypatch.setattr(routes, "load_config", lambda: BOGDANKA)
    html = TestClient(app).get("/widgets/sports").text
    assert "<b>🏐 31.10 Bogdanka LUK Lublin 3:0 GKS Katowice" in html
    assert "<li>🏐 31.10 Skra Bełchatów 0:3" in html


def test_reply_and_widget(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.sports import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    monkeypatch.setattr(routes, "utcnow", lambda: NOW)
    monkeypatch.setattr(routes, "load_config", lambda: CONFIG)
    client = TestClient(app)
    assert client.get("/widgets/sports").text == ""
    with Session(env.engine) as s:
        assert reply_sports(s, NOW, CONFIG, []).startswith("No matches")
    env.run()
    html = client.get("/widgets/sports").text
    assert "Radomiak Radom vs Śląsk Wrocław" in html and "Śląsk Wrocław 2:0 Wieczysta" in html
    with Session(env.engine) as s:
        text = reply_sports(s, NOW, CONFIG, [])
    assert text.startswith("⚽ Mon 02.11 18:00") and "Scores:" in text
