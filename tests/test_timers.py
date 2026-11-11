from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.music import service as music
from assistant.modules.timers import service
from assistant.services.bot import replies
from assistant.services.voice.intents import match

CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 11, 12, 18, tzinfo=ZoneInfo("Europe/Warsaw"))
MIN = timedelta(minutes=1)


@pytest.fixture
def session(tmp_path):
    with Session(make_engine(tmp_path / "db.sqlite")) as s:
        yield s


@pytest.mark.parametrize(
    ("said", "minutes", "label"),
    [
        ("set a timer for 10 minutes", 10, ""),
        ("Set a timer for 10 minutes for the pasta.", 10, "pasta"),
        ("pasta timer 8 minutes", 8, "pasta"),
        ("10m pasta", 10, "pasta"),
        ("timer for an hour and a half", 90, ""),
        ("set a timer for half an hour", 30, ""),
        ("1 hour 30 minutes eggs", 90, "eggs"),
        ("timer for two and a half minutes", 2.5, ""),
        ("90s", 1.5, ""),
        ("timer five minutes", 5, ""),
    ],
)
def test_parse(said, minutes, label):
    assert service.parse(said) == (timedelta(minutes=minutes), label)


def test_parse_rejects_nothing_and_too_long():
    assert service.parse_duration("set a timer") is None
    assert service.parse_duration("timer for 30 hours") is None


def test_ring_stop_and_cancel(session):
    eggs = service.add(session, 5 * MIN, "eggs", NOW)
    service.add(session, 20 * MIN, "pasta", NOW)
    assert service.describe(eggs, NOW + timedelta(seconds=30)) == (
        "eggs timer: 4 minutes 30 seconds left"
    )
    later = NOW + 6 * MIN
    assert [t.label for t in service.ringing(session, later)] == ["eggs"]
    assert [t.label for t in service.stop(session, later)] == ["eggs"]  # the ringing one only
    assert [t.label for t in service.running(session, later)] == ["pasta"]
    assert service.ringing(session, NOW + 24 * MIN) == []  # rings 3 min, then gives up
    assert service.stop(session, later, "pas")[0].label == "pasta"  # cancelled by name


def test_bot(session):
    assert replies.reply_timer(session, NOW, CONFIG, []).startswith("No timers running.")
    assert replies.reply_timer(session, NOW, CONFIG, ["soon"]).startswith("Usage")
    assert replies.reply_timer(session, NOW, CONFIG, ["1h5m", "roast"]) == (
        "⏲️ roast timer set for 1 hour 5 minutes."
    )
    assert replies.reply_timer(session, NOW + MIN, CONFIG, []) == (
        "⏲️ roast timer: 1 hour 4 minutes left"
    )
    assert replies.reply_timer_off(session, NOW, CONFIG, []) == "⏲️ Stopped: roast"
    assert replies.reply_timer_off(session, NOW, CONFIG, []) == "No timer to stop."


def test_voice(session):
    def say(text, at=NOW):
        return match(session, text, at, CONFIG)

    assert say("Set a timer for 10 minutes for the pasta") == "⏲️ pasta timer set for 10 minutes."
    assert say("set a timer") == "For how long? Say for example: set a timer for 10 minutes."
    assert say("How much time is left?") == "⏲️ pasta timer: 10 minutes left"
    music.play(session, [("aaaaaaaaaaa", "song")])
    assert say("stop", NOW + 11 * MIN) == "OK."  # the ringing timer, not the music
    assert music.now_playing(session) is not None
    say("set a timer for 5 minutes")
    assert say("stop", NOW + MIN) == "OK."  # nothing rings: the music stops...
    assert music.now_playing(session) is None
    assert (
        say("how long is left on the timer", NOW + MIN) == "⏲️ Timer: 4 minutes left"
    )  # ...not this
    assert say("cancel the timer", NOW + MIN) == "⏲️ Stopped: timer"
