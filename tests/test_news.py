from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.alarm.service import briefing
from assistant.modules.news import collector, service
from assistant.modules.news.models import NewsDigest, NewsItem
from assistant.services.bot.replies import reply_news

FIXTURES = Path(__file__).parent / "fixtures" / "news"
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
NIGHT = datetime(2026, 10, 7, 1, 45, tzinfo=UTC)  # 03:45 in Warsaw, for 7 October
FEEDS = {"Wrocław": "https://wro/rss", "World": "https://bbc/rss"}
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0}, news={"feeds": FEEDS})


def feed(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_rss():
    items = collector.parse_feed(feed("tuwroclaw.xml"), "Wrocław", NOW)
    assert len(items) == 40
    i = items[0]
    assert i.title == "Chwycił 5-latka i zaczął uciekać. Dzieci wszczęły alarm"  # CDATA, spaces
    assert i.link.startswith("https://tuwroclaw.com/artykul/") and i.feed == "Wrocław"
    assert i.published == datetime(2026, 10, 6, 10, 12, 21, tzinfo=UTC)  # +02:00 → UTC
    assert len(collector.parse_feed(feed("bbc.xml"), "World", NOW)) == 26


def test_parse_atom():
    items = collector.parse_feed(feed("verge_atom.xml"), "Verge", NOW)
    assert len(items) == 10
    assert items[0].link.startswith("https://www.theverge.com/")
    assert items[0].published == datetime(2026, 10, 6, 11, 30, tzinfo=UTC)


def test_parse_skips_broken_items_and_dates():
    rss = b"""<rss><channel>
      <item><title>No link</title></item>
      <item><title>Odd date</title><link>https://x/1</link><pubDate>soon</pubDate></item>
    </channel></rss>"""
    [i] = collector.parse_feed(rss, "X", NOW)
    assert (i.title, i.published) == ("Odd date", NOW)


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    sent, asked = [], []
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text))
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)
    pages = {"https://wro/rss": feed("tuwroclaw.xml"), "https://bbc/rss": feed("bbc.xml")}

    def get(url, **kw):
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return httpx.Response(200, content=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(collector.httpx, "get", get)

    def ask(module, prompt, data, **kw):
        asked.append(data)
        return db.answer

    monkeypatch.setattr(collector.ai, "ask", ask)

    def run(c, now=NOW):
        monkeypatch.setattr(collector, "utcnow", lambda: now)
        with Session(engine) as s, s.begin():
            c.run(s)

    db = type("Db", (), {"engine": engine, "sent": sent, "asked": asked, "pages": pages,
                         "run": staticmethod(run), "answer": "• Tram works.\n• Rain."})  # fmt: skip
    return db


def count(engine) -> int:
    with Session(engine) as s:
        return len(s.scalars(select(NewsItem.id)).all())


def recent(name: str, now: datetime = NOW) -> int:
    """Headlines in a fixture published within the 3 days kept."""
    items = collector.parse_feed(feed(name), "x", now)
    return sum(i.published >= now - collector.KEEP for i in items)


def test_fetch_stores_new_headlines_once(db):
    db.run(collector.NewsCollector())
    stored = recent("tuwroclaw.xml") + recent("bbc.xml")
    assert 0 < stored < 66 and count(db.engine) == stored  # older ones never stored
    db.run(collector.NewsCollector())
    assert count(db.engine) == stored
    db.run(collector.NewsCollector(), NOW + timedelta(days=4))  # older than 3 days: pruned
    assert count(db.engine) == 0


def test_a_broken_feed_does_not_stop_the_others(db):
    db.pages["https://wro/rss"] = httpx.ConnectError("down")
    db.run(collector.NewsCollector())
    assert count(db.engine) == recent("bbc.xml")
    assert db.sent == ["⚠️ News: Wrocław failed: ConnectError: down"]
    db.pages["https://bbc/rss"] = ValueError("x")
    with pytest.raises(RuntimeError):
        db.run(collector.NewsCollector())


def test_digest_once_a_night(db, monkeypatch):
    monkeypatch.setattr(service, "PER_FEED", 3)
    db.run(collector.NewsCollector(), NIGHT - timedelta(hours=1))
    db.run(collector.NewsDigestCollector(), NOW)  # daytime (worker start): nothing
    assert db.asked == []
    db.run(collector.NewsDigestCollector(), NIGHT)
    data = db.asked[0].splitlines()
    assert [line.split("]")[0] for line in data] == ["[World"] * 3 + ["[Wrocław"] * 3  # newest 3
    db.run(collector.NewsDigestCollector(), NIGHT + timedelta(hours=1))  # made: not again
    assert len(db.asked) == 1
    with Session(db.engine) as s:
        assert service.bullets(service.digest(s, date(2026, 10, 7))) == ["Tram works.", "Rain."]


def test_failed_digest_is_retried(db):
    db.run(collector.NewsCollector(), NIGHT - timedelta(hours=1))
    db.answer = None
    with pytest.raises(RuntimeError):  # recorded as a failed run; no digest stored
        db.run(collector.NewsDigestCollector(), NIGHT)
    db.answer = "• Later."
    db.run(collector.NewsDigestCollector(), NIGHT + timedelta(hours=1))
    assert len(db.asked) == 2


def test_news_reply_and_briefing(db):
    with Session(db.engine) as s:
        assert reply_news(s, NOW, CONFIG, []).startswith("No news yet")
    db.run(collector.NewsCollector())
    with Session(db.engine) as s:
        text = reply_news(s, NOW, CONFIG, [])
        assert (
            text.startswith("📰 Latest headlines (no digest yet):") and "(World)\nhttps://" in text
        )
        assert "📰" not in briefing(s, NOW, CONFIG)
    with Session(db.engine) as s, s.begin():
        s.add(
            NewsDigest(day=date(2026, 10, 6), text="• One.\n• Two.\n• Three.\n• Four.", made_at=NOW)
        )
    with Session(db.engine) as s:
        assert reply_news(s, NOW, CONFIG, []).startswith("📰 Today's digest\n• One.\n• Two.")
        assert "📰 News:\n  One.\n  Two.\n  Three." in briefing(s, NOW, CONFIG)
        assert "Four" not in briefing(s, NOW, CONFIG)
    off = Config(location={"latitude": 51.1, "longitude": 17.0})
    with Session(db.engine) as s:
        assert reply_news(s, NOW, off, []).startswith("News is off")


def test_latest_takes_two_per_feed(db):
    db.run(collector.NewsCollector())
    with Session(db.engine) as s:
        feeds = [i.feed for i in service.latest(s, NOW)]
    assert sorted(feeds) == ["World", "World", "Wrocław", "Wrocław"]


def test_widget_and_tap(db, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.news import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: db.engine)
    monkeypatch.setattr(routes, "utcnow", lambda: NOW)
    client = TestClient(app)
    assert client.get("/widgets/news").text == ""
    db.run(collector.NewsCollector())
    html = client.get("/widgets/news").text
    assert "latest headlines" in html and "digest" not in html
    item_id = html.split('hx-post="/api/news/send/')[1].split('"')[0]
    sent = []
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    assert client.post(f"/api/news/send/{item_id}").text == "✓ sent to Telegram"
    assert sent[0].startswith("🔗 ") and "\nhttps://" in sent[0]
