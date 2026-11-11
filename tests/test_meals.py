from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.biedronka import service as biedronka
from assistant.modules.meals import collector, service
from assistant.services.bot import replies
from assistant.services.voice.intents import match

CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0}, meals={"taste": "no mushrooms"})
NOW = datetime(2026, 11, 16, 4, 55, tzinfo=UTC)  # Monday 05:55 in Warsaw
ANSWER = (
    "Here you go:\n"
    "Salmon pasta | Łosoś wędzony, makaron | Toss pasta with salmon, cream and dill.\n"
    "Cod with peppers | DORSZ, PAPRYKA | Roast cod and sliced peppers 20 minutes at 200°C.\n"
    "Avocado eggs | Awokado | Bake eggs in halved avocados.\n"
    "Extra | x | y"
)


def offer(product, text, price, checked=True):
    o = biedronka.Offer(product, text, price, None, None, date(2026, 11, 19), 1, "https://i/1")
    o.checked = checked
    return o


@pytest.fixture
def env(tmp_path, monkeypatch):
    asked = []
    offers = [offer("łosoś", "Łosoś wędzony", 11.99), offer("dorsz", "DORSZ", 6.99),
              offer("ryb", "Mango", 5.99, checked=False)]  # fmt: skip
    monkeypatch.setattr(collector.biedronka, "offers", lambda s, today: offers)
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)
    monkeypatch.setattr(collector, "utcnow", lambda: NOW)
    monkeypatch.setattr(collector.ai, "at_night", lambda now: True)
    monkeypatch.setattr(
        collector.ai, "ask", lambda module, prompt, data: asked.append((prompt, data)) or ANSWER
    )
    with Session(make_engine(tmp_path / "db.sqlite")) as s:
        yield s, asked


def test_weekly_ideas_from_checked_offers(env):
    session, asked = env
    collector.MealsCollector().run(session)
    collector.MealsCollector().run(session)  # once a week
    assert len(asked) == 1
    prompt, data = asked[0]
    assert "no mushrooms" in prompt
    assert data.splitlines() == [
        "Łosoś wędzony (łosoś) | 11,99 zł | until 19.11",
        "DORSZ (dorsz) | 6,99 zł | until 19.11",
    ]  # the unchecked offer isn't sent
    today = NOW.date()
    assert [m.name for m in service.ideas(session, today)] == [
        "Salmon pasta", "Cod with peppers", "Avocado eggs"
    ]  # fmt: skip
    assert replies.reply_meals(session, NOW, CONFIG, []).startswith(
        "🍳 Salmon pasta\n   uses: Łosoś wędzony, makaron\n   Toss pasta"
    )
    assert match(session, "What should I cook tonight?", NOW, CONFIG).startswith("🍳 Salmon")
    assert service.ideas(session, date(2026, 11, 23)) == []  # next week: new ones


def test_failed_call_retries_and_no_offers_skips(env, monkeypatch):
    session, asked = env
    monkeypatch.setattr(collector.ai, "ask", lambda *a: None)
    collector.MealsCollector().run(session)
    assert service.ideas(session, NOW.date()) == []
    assert replies.reply_meals(session, NOW, CONFIG, []).startswith("No dinner ideas")
