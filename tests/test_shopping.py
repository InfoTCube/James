from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.biedronka import service as biedronka
from assistant.modules.shopping import service
from assistant.services.bot import replies
from assistant.services.voice.intents import match

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 11, 11, 18, tzinfo=TZ)
TODAY = date(2026, 11, 11)


def offer(product, text, price):
    return biedronka.Offer(product, text, price, None, None, date(2026, 11, 14), 1, "https://i/1")


@pytest.fixture
def session(tmp_path, monkeypatch):
    deals = [offer("mleko", "Mleko UHT 3,2%", 2.99), offer("mleko", "Mleko Łaciate", 3.49),
             offer("ryb", "Dorsz filet", 6.99)]  # fmt: skip
    monkeypatch.setattr(service.biedronka, "offers", lambda s, today: deals)
    with Session(make_engine(tmp_path / "db.sqlite")) as s:
        yield s


def test_list_add_flag_tick_off_and_clear(session):
    assert replies.reply_list(session, NOW, CONFIG, []) == "🛒 The list is empty."
    assert replies.reply_list(session, NOW, CONFIG, ["Mleko,", "chleb,", "dorsz"]) == (
        "🛒 Added: Mleko, chleb, dorsz\n"
        "1. Mleko  🐞 2,99 zł until 14.11\n2. chleb\n3. dorsz  🐞 6,99 zł until 14.11"
    )
    assert replies.reply_list(session, NOW, CONFIG, ["mleko"]).startswith("🛒 Already on")
    assert replies.reply_got(session, NOW, CONFIG, ["mle,", "3"]) == "✅ Mleko, dorsz (1 left)"
    assert replies.reply_got(session, NOW, CONFIG, ["kawa"]) == "Not on the list."
    assert replies.reply_got(session, NOW, CONFIG, ["all"]) == "🛒 Cleared 1 items."


def test_voice(session):
    def say(text):
        return match(session, text, NOW, CONFIG)

    assert say("Add milk and bread to my shopping list") == "🛒 Added: milk, bread"
    assert say("add eggs to the list") == "🛒 Added: eggs"
    assert say("What's on my shopping list?").splitlines()[1:] == ["1. milk", "2. bread", "3. eggs"]
    assert say("Remove bread from my shopping list") == "✅ bread (2 left)"
    assert say("Clear the shopping list") == "🛒 Cleared 2 items."
    assert say("Add dentist to my calendar tomorrow at 3 pm") != "🛒 Added: dentist"
