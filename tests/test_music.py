from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.music import client, service
from assistant.services.bot import replies
from assistant.services.voice import intents

CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 11, 11, 20, tzinfo=ZoneInfo("Europe/Warsaw"))
TRACKS = [
    ("aaaaaaaaaaa", "Małomiasteczkowy"),
    ("bbbbbbbbbbb", "Nie ma fal"),
    ("ccccccccccc", "Trofea"),
]


@pytest.fixture
def session(tmp_path):
    with Session(make_engine(tmp_path / "db.sqlite")) as s:
        yield s


def test_queue_play_skip_pause_stop(session):
    service.play(session, TRACKS)
    assert service.now_playing(session).title == "Małomiasteczkowy"
    assert [t.title for t in service.up_next(session)] == ["Nie ma fal", "Trofea"]
    assert service.skip(session, "zzzzzzzzzzz").title == "Małomiasteczkowy"  # already moved on
    assert service.skip(session, "aaaaaaaaaaa").title == "Nie ma fal"
    assert service.set_paused(session, True).paused
    service.play(session, TRACKS[2:])  # a new /play replaces the queue
    assert [t.title for t in service.up_next(session, 10)] == []
    assert service.now_playing(session).title == "Trofea"
    assert service.stop(session) and service.now_playing(session) is None
    assert not service.stop(session)


def test_bot_replies(session, monkeypatch):
    monkeypatch.setattr(replies.music_client, "find", lambda q: TRACKS if q == "podsiadło" else [])
    assert replies.reply_play(session, NOW, CONFIG, ["nothing"]) == "Nothing found on YouTube."
    assert replies.reply_play(session, NOW, CONFIG, ["podsiadło"]) == (
        "▶️ Małomiasteczkowy\nThen 2 more from its mix."
    )
    assert replies.reply_pause(session, NOW, CONFIG, []) == "⏸️ Paused"
    assert replies.reply_play(session, NOW, CONFIG, []) == (
        "▶️ Małomiasteczkowy\n· Nie ma fal\n· Trofea"
    )
    assert not service.now_playing(session).paused
    assert replies.reply_skip(session, NOW, CONFIG, []) == "▶️ Nie ma fal"
    assert replies.reply_stop(session, NOW, CONFIG, []) == "⏹️ Stopped"
    assert replies.reply_skip(session, NOW, CONFIG, []) == "That was the last one."


def test_voice_intents(session):
    def say(text):
        return intents.match(session, text, NOW, CONFIG)

    service.play(session, TRACKS)
    assert say("what's playing?") == "This is Małomiasteczkowy."
    assert say("pause the music") == "⏸️ Paused"
    assert say("resume") == "▶️ Małomiasteczkowy\n· Nie ma fal\n· Trofea"
    assert say("pause") == "⏸️ Paused"
    assert say("next song") == "▶️ Nie ma fal"
    assert say("skip this") == "▶️ Trofea"
    assert say("stop the music") == "⏹️ Stopped"
    assert intents._find("when is my next tram")[0] is not intents._find("next song")[0]


def test_voice_stop_stops_music_when_nothing_rings(session):
    service.play(session, TRACKS)
    assert intents.match(session, "stop", NOW, CONFIG) == "OK."
    assert service.now_playing(session) is None
    assert intents.match(session, "stop", NOW, CONFIG) == "Nothing is ringing."


def test_voice_play_resumes_or_searches(session, monkeypatch):
    asked = []
    monkeypatch.setattr(replies.music_client, "find", lambda q: asked.append(q) or TRACKS)
    intents.match(session, "play Dawid Podsiadło.", NOW, CONFIG)
    service.set_paused(session, True)
    intents.match(session, "play music", NOW, CONFIG)
    assert asked == ["Dawid Podsiadło"] and not service.now_playing(session).paused


class FakeYDL:
    """Answers like yt-dlp with extract_flat: a search, a video, a playlist, a mix."""

    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        if url.startswith("ytsearch1:"):
            return {"entries": [{"id": "aaaaaaaaaaa", "title": "Małomiasteczkowy"}]}
        if "list=RD" in url:
            return {
                "entries": [
                    {"id": "aaaaaaaaaaa", "title": "x"},
                    {"id": "bbbbbbbbbbb", "title": "Nie ma fal"},
                ]
            }
        if "playlist" in url:
            return {"entries": [{"id": "ccccccccccc", "title": "Trofea"}]}
        return {"id": "ccccccccccc", "title": "Trofea"}


def test_find(monkeypatch):
    monkeypatch.setattr(client, "YoutubeDL", FakeYDL)
    assert client.find("podsiadło") == TRACKS[:2]  # the mix, without the first one again
    assert client.find("https://www.youtube.com/playlist?list=PL1") == [TRACKS[2]]
    assert client.find("https://youtu.be/ccccccccccc") == [
        TRACKS[2],
        ("aaaaaaaaaaa", "x"),
        TRACKS[1],
    ]
