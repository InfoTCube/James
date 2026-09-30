from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.notes import service
from assistant.services.bot.replies import reply_note, reply_note_del, reply_notes

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 10, 1, 12, tzinfo=TZ)


@pytest.fixture
def db(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        for text in ["Kupić kabel HDMI", "Żółw potrzebuje sałaty", "Rabat 50% w Biedronce"]:
            service.add_note(s, text, NOW)
    return engine


def test_search_ignores_case_and_diacritics(db):
    with Session(db) as s:
        assert [n.text for n in service.search_notes(s, "zolw SALATY")] == [
            "Żółw potrzebuje sałaty"
        ]
        assert [n.text for n in service.search_notes(s, "kabel")] == ["Kupić kabel HDMI"]
        assert service.search_notes(s, "kabel żółw") == []  # every word must match
        assert [n.text for n in service.search_notes(s, "50%")] == ["Rabat 50% w Biedronce"]
        assert service.search_notes(s, "%") != []  # literal %, matches the one with a %
        assert [n.id for n in service.search_notes(s)] == [3, 2, 1]  # newest first


def test_bot_note_commands(db):
    with Session(db) as s, s.begin():
        assert reply_note(s, NOW, CONFIG, ["oddać", "książkę"]) == "📝 Saved #4"
        assert reply_note(s, NOW, CONFIG, []) == "Usage: /note buy HDMI cable"
    with Session(db) as s:
        assert reply_notes(s, NOW, CONFIG, ["ksiazke"]) == "#4 01.10 oddać książkę"
        assert reply_notes(s, NOW, CONFIG, ["nothing"]) == "No matching notes."
    with Session(db) as s, s.begin():
        assert reply_note_del(s, NOW, CONFIG, ["#4"]) == "🗑️ Deleted #4: oddać książkę"
        assert reply_note_del(s, NOW, CONFIG, ["4"]) == "No such note."
    with Session(db) as s, s.begin():
        assert reply_note(s, NOW, CONFIG, ["new"]) == "📝 Saved #5"  # ids are never reused
