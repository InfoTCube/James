import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Config, FilmsConfig
from assistant.core.db import make_engine
from assistant.modules.films import collector, service
from assistant.modules.films.models import Film
from assistant.services.bot.replies import reply_films, reply_unwatched, reply_watched

FIXTURES = Path(__file__).parent / "fixtures" / "films"
POPULAR = json.loads((FIXTURES / "popular.json").read_text(encoding="utf-8"))
PACKAGES = json.loads((FIXTURES / "packages.json").read_text(encoding="utf-8"))
NIGHT = datetime(2026, 11, 2, 1, 15, tzinfo=UTC)  # Monday 02:15 in Warsaw
DAY = datetime(2026, 11, 2, 12, tzinfo=UTC)
MONDAY = date(2026, 11, 2)
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0}, films={"taste": "smart sci-fi"})
HAIL_MARY = 1486587
AVATAR = 358


def parse(**cfg) -> list[Film]:
    seen = cfg.pop("seen", set())
    return collector.parse_films(POPULAR["data"], FilmsConfig(**cfg), seen, MONDAY)


def test_parse_recorded_response():
    films = parse()
    assert len(films) == 5
    hail = next(f for f in films if f.id == HAIL_MARY)
    assert (hail.title, hail.year, hail.rating) == ("Project Hail Mary", 2026, 8.2)
    assert hail.providers == "Amazon Prime Video"
    assert hail.link == "https://www.justwatch.com/pl/film/projekt-hail-mary"
    assert films[0].providers == "Polsat Box Go"  # listed twice in the response: once


def test_parse_keeps_your_services_and_drops_seen():
    assert {f.title for f in parse(providers=["disney plus"])} == {"Avatar: Fire and Ash"}
    assert AVATAR not in {f.id for f in parse(seen={AVATAR})}


def test_package_codes():
    packages = PACKAGES["data"]["packages"]
    assert collector.package_codes(packages, ["netflix", "Nope", "HBO MAX"]) == ["nfx", "mxx"]
    with pytest.raises(ValueError):
        collector.package_codes(packages, ["Nope"])


def fake_post(calls: list[dict]):
    def post(url, json=None, **kw):
        calls.append(json)
        body = PACKAGES if "packages(" in json["query"] else POPULAR
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    return post


def test_fetch_filters_on_your_services(monkeypatch):
    calls = []
    monkeypatch.setattr(collector.httpx, "post", fake_post(calls))
    films = collector.fetch_candidates(
        FilmsConfig(providers=["Netflix", "Disney Plus"], min_rating=7.5), set(), MONDAY
    )
    assert [f.id for f in films] == [AVATAR]
    variables = calls[1]["variables"]
    assert variables["filter"]["packages"] == ["nfx", "dnp"]
    assert variables["filter"]["imdbScore"] == {"min": 7.5}
    assert variables["country"] == "PL"


def test_graphql_errors_fail_the_run(monkeypatch):
    def post(url, **kw):
        return httpx.Response(
            200, json={"errors": [{"message": "bad"}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(collector.httpx, "post", post)
    with pytest.raises(RuntimeError):
        collector.graphql("query")


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    calls, asked = [], []
    monkeypatch.setattr(collector.httpx, "post", fake_post(calls))
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)

    class Env:
        answer = f"{HAIL_MARY} | Clever first contact.\n999 | Invented.\n"

        def __init__(self):
            self.engine, self.calls, self.asked = engine, calls, asked

        def run(self, now):
            monkeypatch.setattr(collector, "utcnow", lambda: now)
            monkeypatch.setattr(
                collector.ai, "at_night", lambda n: 1 <= n.astimezone(CONFIG.tz).hour < 6
            )
            monkeypatch.setattr(
                collector.ai, "ask", lambda m, p, d, **k: asked.append((p, d)) or self.answer
            )
            with Session(engine) as s, s.begin():
                collector.FilmsCollector().run(s)

        def titles(self):
            with Session(engine) as s:
                return [f.title for f in service.suggestions(s)]

    return Env()


BEST_RATED = ["Project Hail Mary", "Spider-Man: Brand New Day", "Marty Supreme"]


def test_no_suggestions_before_the_first_run(env):
    with Session(env.engine) as s:
        assert reply_films(s, DAY, CONFIG, []).startswith("No film suggestions yet")


def test_daytime_only_fetches_and_shows_the_best_rated(env):
    env.run(DAY)
    assert env.asked == []
    assert env.titles() == BEST_RATED


def test_night_picks_once_and_ignores_invented_ids(env):
    env.run(NIGHT)
    prompt, data = env.asked[0]
    assert "smart sci-fi" in prompt and f"{HAIL_MARY} | Project Hail Mary (2026) | 8.2 |" in data
    assert env.titles() == ["Project Hail Mary"]
    fetched = len(env.calls)
    env.run(NIGHT + timedelta(hours=1))  # already picked: neither AI nor JustWatch
    assert len(env.asked) == 1 and len(env.calls) == fetched
    with Session(env.engine) as s:
        assert reply_films(s, DAY, CONFIG, []) == (
            "🎬 Project Hail Mary (2026) · 8.2 · Amazon Prime Video\nClever first contact.\n"
            "https://www.justwatch.com/pl/film/projekt-hail-mary"
        )


def test_failed_ai_keeps_candidates_and_retries_without_refetching(env):
    env.answer = None
    env.run(NIGHT)
    assert env.titles() == BEST_RATED  # raw data meanwhile
    fetched = len(env.calls)
    env.answer = f"{AVATAR} | Big and blue."
    env.run(NIGHT + timedelta(hours=1))
    assert env.titles() == ["Avatar: Fire and Ash"] and len(env.calls) == fetched


def test_next_week_replaces_candidates_and_never_repeats_a_pick(env):
    env.run(NIGHT)
    env.answer = f"{AVATAR} | Next one."
    env.run(NIGHT + timedelta(days=7))
    assert env.titles() == ["Avatar: Fire and Ash"]
    assert f"{HAIL_MARY} |" not in env.asked[1][1]
    with Session(env.engine) as s:
        picked = {f.id: f.week for f in s.scalars(select(Film).where(Film.picked))}
    assert picked == {HAIL_MARY: MONDAY, AVATAR: MONDAY + timedelta(days=7)}


def mark(env, title: str, when: datetime = DAY) -> None:
    with Session(env.engine) as s, s.begin():
        service.mark_watched(s, title, when)


def test_watched_film_is_filtered_and_shown_to_the_ai_as_taste(env):
    mark(env, "PROJECT  hail mary")  # any case and spacing
    mark(env, "Boże Ciało", DAY + timedelta(minutes=1))  # not a candidate: for the AI only
    env.answer = f"{HAIL_MARY} | Should be ignored.\n{AVATAR} | Big and blue."
    env.run(NIGHT)
    _, data = env.asked[0]
    assert data.startswith("Already watched:\nBoże Ciało\nPROJECT hail mary\n\nCandidates:\n")
    assert f"{HAIL_MARY} |" not in data
    assert env.titles() == ["Avatar: Fire and Ash"]  # the AI can't pick it anyway


def test_marking_a_pick_replaces_it_and_can_be_undone(env):
    env.run(NIGHT)
    assert env.titles() == ["Project Hail Mary"]
    mark(env, "Project Hail Mary")
    assert env.titles() == ["Spider-Man: Brand New Day", "Marty Supreme", "The Invite"]
    with Session(env.engine) as s, s.begin():
        assert service.unmark_watched(s, "project hail mary")
        s.flush()
        assert not service.unmark_watched(s, "project hail mary")
    assert env.titles() == ["Project Hail Mary"]


def test_watched_commands(env):
    with Session(env.engine) as s, s.begin():
        assert reply_watched(s, DAY, CONFIG, []).startswith("No films marked")
        assert reply_watched(s, DAY, CONFIG, ["The", "Matrix"]).startswith("✓ Watched: The Matrix")
        s.flush()
        assert reply_watched(s, DAY, CONFIG, []) == "🎬 Watched:\nThe Matrix"
        assert reply_unwatched(s, DAY, CONFIG, ["the", "matrix"]) == "Removed the matrix."
        s.flush()
        assert reply_unwatched(s, DAY, CONFIG, ["the", "matrix"]) == "the matrix isn't marked."


def test_card_button_marks_the_film(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.films import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    env.run(NIGHT)
    client = TestClient(app)
    assert f'hx-post="/api/films/watched/{HAIL_MARY}"' in client.get("/widgets/films").text
    html = client.post(f"/api/films/watched/{HAIL_MARY}").text
    assert "Project Hail Mary" not in html and "Spider-Man" in html


def test_widget_and_tap(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.films import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    client = TestClient(app)
    assert client.get("/widgets/films").text == ""
    env.run(NIGHT)
    html = client.get("/widgets/films").text
    assert "Project Hail Mary" in html and "Clever first contact." in html
    sent = []
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    assert client.post(f"/api/films/send/{HAIL_MARY}").text == "✓ sent to Telegram"
    assert sent[0].startswith("🎬 Project Hail Mary") and "\nhttps://" in sent[0]
    assert client.post("/api/films/send/5").text == "✗ gone"
