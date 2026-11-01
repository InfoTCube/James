import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from assistant.core.ai import parse_picks
from assistant.core.config import BooksConfig, Config
from assistant.core.db import make_engine
from assistant.modules.books import collector, service
from assistant.services.bot.replies import reply_books, reply_read, reply_unread

RAW = (Path(__file__).parent / "fixtures" / "books" / "search.json").read_bytes()
NIGHT = datetime(2026, 11, 1, 1, 25, tzinfo=UTC)  # 02:25 in Warsaw
DAY = datetime(2026, 11, 1, 12, tzinfo=UTC)
MONTH = date(2026, 11, 1)
CONFIG = Config(
    location={"latitude": 51.1, "longitude": 17.0},
    books={"subjects": ["science fiction", "fantasy"], "taste": "hard sci-fi", "min_ratings": 50},
)


def test_parse_picks():
    answer = (
        "• OL1W | Good.\nOL9W | Invented.\nno separator\nOL1W | Again.\n`OL2W`|Fine\nOL3W | Over"
    )
    assert parse_picks(answer, {"OL1W", "OL2W", "OL3W"}, 2) == {"OL1W": "Good.", "OL2W": "Fine"}
    assert parse_picks("", {"OL1W"}, 2) == {}


def test_parse_filters_on_ratings_language_and_seen():
    data = json.loads(RAW)
    books = collector.parse_books(data, BooksConfig(min_ratings=50), {"OL19800273W"}, MONTH)
    # Saga has too few ratings, Tiamat's Wrath is seen
    assert [b.title for b in books] == ["Project Hail Mary", "Six of Crows", "Network Effect"]
    hail = books[0]
    assert (hail.id, hail.author, hail.year) == ("OL21745884W", "Andy Weir", 2021)
    assert hail.link == "https://openlibrary.org/works/OL21745884W"
    polish = collector.parse_books(
        data, BooksConfig(min_ratings=0, languages=["pol"]), set(), MONTH
    )
    assert [b.title for b in polish] == ["Tiamat's Wrath"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    gets, asked = [], []

    def get(url, params=None, **kw):
        gets.append(params["q"])
        return httpx.Response(200, content=RAW, request=httpx.Request("GET", url))

    monkeypatch.setattr(collector.httpx, "get", get)
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)

    class Env:
        answer = "OL21745884W | Hard science, big heart."

        def __init__(self):
            self.engine, self.gets, self.asked = engine, gets, asked

        def run(self, now):
            monkeypatch.setattr(collector, "utcnow", lambda: now)
            monkeypatch.setattr(
                collector.ai, "at_night", lambda n: 1 <= n.astimezone(CONFIG.tz).hour < 6
            )
            monkeypatch.setattr(
                collector.ai, "ask", lambda m, p, d, **k: asked.append((p, d)) or self.answer
            )
            with Session(engine) as s, s.begin():
                collector.BooksCollector().run(s)

        def titles(self):
            with Session(engine) as s:
                return [b.title for b in service.suggestions(s)]

    return Env()


def test_off_without_subjects(env, monkeypatch):
    off = Config(location={"latitude": 51.1, "longitude": 17.0})
    monkeypatch.setattr(collector, "load_config", lambda: off)
    env.run(NIGHT)
    assert env.gets == []
    with Session(env.engine) as s:
        assert reply_books(s, DAY, off, []).startswith("No book suggestions")


def test_daytime_fetches_each_subject_and_shows_the_best_rated(env):
    env.run(DAY)
    assert env.asked == []
    assert env.gets[0] == 'subject:"science fiction" first_publish_year:[2000 TO 2026]'
    assert len(env.gets) == 2
    assert env.titles() == ["Six of Crows", "Project Hail Mary", "Tiamat's Wrath"]


def test_night_picks_once_a_month(env):
    env.run(NIGHT)
    prompt, data = env.asked[0]
    assert "hard sci-fi" in prompt and "OL21745884W | Project Hail Mary | Andy Weir | 2021" in data
    assert data.count("Project Hail Mary") == 1  # both subjects returned it: once
    assert env.titles() == ["Project Hail Mary"]
    env.run(NIGHT + timedelta(days=5))  # same month: nothing new
    assert len(env.asked) == 1 and len(env.gets) == 2
    with Session(env.engine) as s:
        assert reply_books(s, DAY, CONFIG, []) == (
            "📚 Project Hail Mary by Andy Weir (2021) · 4.5\nHard science, big heart.\n"
            "https://openlibrary.org/works/OL21745884W"
        )


def test_failed_ai_keeps_candidates_for_the_retry(env):
    env.answer = None
    env.run(NIGHT)
    assert len(env.titles()) == 3
    env.answer = "OL17332479W | Heist fun."
    env.run(NIGHT + timedelta(hours=1))
    assert env.titles() == ["Six of Crows"] and len(env.gets) == 2


def test_next_month_never_repeats_a_pick(env):
    env.run(NIGHT)
    env.answer = "OL17332479W | Next."
    env.run(NIGHT + timedelta(days=31))
    assert env.titles() == ["Six of Crows"]
    assert "Project Hail Mary" not in env.asked[1][1]


def test_read_book_is_filtered_and_shown_to_the_ai_as_taste(env):
    with Session(env.engine) as s, s.begin():
        service.mark_read(s, "project hail mary", DAY)
        service.mark_read(s, "Atomowe nawyki", DAY + timedelta(minutes=1))
    env.answer = "OL21745884W | Should be ignored.\nOL17332479W | Heist fun."
    env.run(NIGHT)
    _, data = env.asked[0]
    assert data.startswith("Already read:\nAtomowe nawyki\nproject hail mary\n\nCandidates:\n")
    assert "OL21745884W" not in data
    assert env.titles() == ["Six of Crows"]


def test_read_commands_and_undo(env):
    with Session(env.engine) as s, s.begin():
        assert reply_read(s, DAY, CONFIG, []).startswith("No books marked")
        assert reply_read(s, DAY, CONFIG, ["Dune"]).startswith("✓ Read: Dune")
        s.flush()
        assert reply_read(s, DAY, CONFIG, []) == "📚 Read:\nDune"
        assert reply_unread(s, DAY, CONFIG, ["dune"]) == "Removed dune."
        s.flush()
        assert reply_unread(s, DAY, CONFIG, ["dune"]) == "dune isn't marked."


def test_card_button_marks_the_book(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.books import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    env.run(NIGHT)
    html = TestClient(app).post("/api/books/read/OL21745884W").text
    assert "Project Hail Mary" not in html and "Six of Crows" in html


def test_widget_and_tap(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.books import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    client = TestClient(app)
    assert client.get("/widgets/books").text == ""
    env.run(NIGHT)
    html = client.get("/widgets/books").text
    assert "Project Hail Mary" in html and "Hard science" in html
    sent = []
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    assert client.post("/api/books/send/OL21745884W").text == "✓ sent to Telegram"
    assert sent[0].startswith("📚 Project Hail Mary")
