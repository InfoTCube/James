from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.alarm import service as alarm
from assistant.modules.calendar import client as calendar_client
from assistant.modules.notes import service as notes
from assistant.services.voice.intents import alarm_time, match, parse_event, spoken

TZ = ZoneInfo("Europe/Warsaw")
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
NOW = datetime(2026, 10, 6, 22, 15, tzinfo=TZ)


@pytest.fixture
def session(tmp_path):
    with Session(make_engine(tmp_path / "db.sqlite")) as s, s.begin():
        yield s


def test_alarm_time_from_whisper_text():
    assert alarm_time(" up at 7.30 a.m.") == "7:30"
    assert alarm_time(" at 7:30 PM") == "19:30"
    assert alarm_time(" up at seven o'clock") == "7:00"
    assert alarm_time(" for 6 45") == "6:45"
    assert alarm_time(" at 12 a.m.") == "0:00"
    assert alarm_time(" at 7 a meeting") == "7:00"  # "a" alone isn't a.m.
    assert alarm_time(" please") is None
    assert alarm_time(" at 5pm") == "17:00"
    assert alarm_time(" at 6:15am") == "6:15"
    assert alarm_time(" at seventeen") is None  # not "seven"


def test_set_alarm(session):
    assert match(session, "Wake me up at 7.30 a.m.", NOW, CONFIG) == "Alarm set for 07:30 tomorrow."
    [a] = alarm.upcoming_alarms(session, NOW)
    assert a.at == datetime(2026, 10, 7, 7, 30, tzinfo=TZ)
    assert match(session, "Set an alarm.", NOW, CONFIG).startswith("At what time?")


def test_stop_only_when_ringing(session):
    assert match(session, "Stop.", NOW, CONFIG) == "Nothing is ringing."
    a = alarm.add_alarm(session, NOW - timedelta(minutes=1))
    a.rung_at = NOW - timedelta(seconds=30)
    assert match(session, "Snooze!", NOW, CONFIG) == "OK."
    assert a.at == NOW + alarm.SNOOZE


def test_note_keeps_the_words(session):
    assert match(session, "Take a note: buy HDMI cable.", NOW, CONFIG).startswith("📝 Saved")
    assert match(session, "Remember to call Mateusz", NOW, CONFIG).startswith("📝 Saved")
    assert [n.text for n in notes.search_notes(session)] == ["call Mateusz", "buy HDMI cable"]


def test_routing(session):
    assert match(session, "Do I need an umbrella today?", NOW, CONFIG) == "No forecast yet."
    assert match(session, "When is my next tram?", NOW, CONFIG).startswith("Nothing planned")
    assert match(session, "What's on my calendar?", NOW, CONFIG) == "Nothing in the calendar today."
    tomorrow = "What do I have in my agenda tomorrow?"
    assert match(session, tomorrow, NOW, CONFIG) == "Nothing in the calendar tomorrow."
    fridays = ["What's on Friday?", "What is on Friday?", "Do I have anything on Friday?",
               "What am I doing on Friday?", "Am I free on Friday?", "And Friday?"]  # fmt: skip
    for friday in fridays:
        assert match(session, friday, NOW, CONFIG) == "Nothing in the calendar on Friday 9 October."
    assert match(session, "What time is it?", NOW, CONFIG) == "It's 22:15, Tuesday 6 October."
    assert match(session, "What is the capital of Peru?", NOW, CONFIG) is None  # → AI
    nothing = "You don't need to go out again today."
    assert match(session, "What time do I go out today?", NOW, CONFIG) == nothing
    assert match(session, "When should I leave?", NOW, CONFIG) == nothing
    assert match(session, "When do I go tomorrow?", NOW, CONFIG).endswith("out tomorrow.")


def test_spoken_drops_emoji_and_links():
    text = "⭐ Junior .NET – Acme\nremote · 40 h\nhttps://x.pl/1\n\n⚠️ 12° → feels ~10°"
    assert spoken(text) == [
        "Junior .NET, Acme",
        "remote, 40 h",
        "12 degrees to feels about 10 degrees",
    ]
    assert len(spoken("\n".join(map(str, range(20))))) == 8


PLACES = CONFIG.model_copy(update={"places": {"uni": {"stops": ["PL. GRUNWALDZKI"]}}})


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(
    "said, title, start, end",
    [
        ("Add dentist to my calendar tomorrow at 3 p.m.", "Dentist", at(7, 15), at(7, 16)),
        (
            "Add an event called team lunch on Friday at 12.30 for 2 hours",
            "Team lunch",
            at(9, 12, 30),
            at(9, 14, 30),
        ),  # fmt: skip
        ("Add a meeting with Kasia at 10", "Meeting with Kasia", at(7, 10), at(7, 11)),
        ("Schedule gym on Tuesday at 7 for half an hour", "Gym", at(13, 19), at(13, 19, 30)),
        (
            "Put Mateusz's party on the 12th of October in my calendar",
            "Mateusz's party",
            date(2026, 10, 12),
            date(2026, 10, 13),
        ),  # fmt: skip
        (
            "Add an event dinner October 3rd at 8 pm",
            "Dinner",
            datetime(2027, 10, 3, 20, tzinfo=TZ),
            datetime(2027, 10, 3, 21, tzinfo=TZ),
        ),  # fmt: skip
        ("Add a calendar event", "", None, None),
    ],
)
def test_parse_event(said, title, start, end):
    assert parse_event(said, NOW, CONFIG) == (title, start, end, None)


def test_parse_event_place_alias():
    e = parse_event("Add study group at uni tomorrow at 9 a.m.", NOW, PLACES)
    assert (e.title, e.start, e.location) == ("Study group", at(7, 9), "uni")


def test_add_event(session, monkeypatch):
    added = []
    monkeypatch.setattr(calendar_client, "add_event", lambda *a: added.append(a))
    said = "Add dentist to my calendar tomorrow at 3 pm"
    assert match(session, said, NOW, CONFIG) == "Added Dentist, tomorrow at 15:00."
    assert added == [("Dentist", at(7, 15), at(7, 16), None)]
    assert match(session, "Add dentist to my calendar", NOW, CONFIG).startswith("When?")

    def offline(*a):
        raise ConnectionError

    monkeypatch.setattr(calendar_client, "add_event", offline)
    assert match(session, said, NOW, CONFIG) == "Sorry, I couldn't reach Google Calendar."


def test_cancel_alarm(session):
    assert match(session, "Cancel my alarm", NOW, CONFIG) == "No alarms set."
    alarm.add_alarm(session, at(7, 7, 30))
    alarm.add_alarm(session, at(7, 19, 30))
    alarm.add_alarm(session, at(8, 9))
    assert match(session, "Cancel the alarm", NOW, CONFIG) == (
        "Which one? You have alarms at 07:30, 19:30 and 09:00."
    )
    assert match(session, "Cancel the 9 o'clock alarm", NOW, CONFIG) == "Cancelled the 09:00 alarm."
    assert match(session, "Delete the 7.30 alarm", NOW, CONFIG).startswith("Which one?")
    assert match(session, "Delete the alarm for 7.30 p.m.", NOW, CONFIG) == (
        "Cancelled the 19:30 alarm."
    )
    assert match(session, "Turn off all alarms", NOW, CONFIG) == "Cancelled 1 alarm."
    assert alarm.upcoming_alarms(session, NOW) == []


def test_films_books_and_sports_intents():
    from assistant.services.voice import intents

    def handler(text):
        return intents._find(text)[0]

    assert handler("any good movies this week") is intents._films
    assert handler("what film should I watch") is intents._films
    assert handler("recommend some books") is intents._books
    assert handler("what should I read") is intents._books
    assert handler("when is the next match") is intents._sports
    assert handler("what are the football scores") is intents._sports
    assert handler("when is my next tram") is not intents._sports
    assert handler("I've watched Inception") is intents._marked_film
    assert handler("I have seen The Matrix") is intents._marked_film
    assert handler("I've read Atomic Habits") is intents._marked_book
    assert handler("I already read Atomic Habits") is intents._marked_book


def test_voice_state_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from assistant.services.voice import routes

    client = TestClient(FastAPI(routes=routes.router.routes))
    state = {"state": "speaking", "text": "weather?", "reply": ["12 degrees"], "line": 0}
    assert client.post("/api/voice/state", json=state).status_code == 200
    assert routes._state.reply == ["12 degrees"]
    assert client.post("/api/voice/state", json={"state": "dancing"}).status_code == 422
    assert client.post("/api/voice/state", json={"state": "idle", "level": 7}).status_code == 422


def test_leave_earlier(session):
    from assistant.modules.calendar.models import CalendarEvent
    from assistant.modules.mpk import service as mpk
    from assistant.services.bot.replies import reply_early

    session.add(CalendarEvent(id="vb", start=at(7, 17, 10), end=at(7, 19), title="Siatkówka 🏐",
                              location="SWFiS", all_day=False, kind="default"))  # fmt: skip
    session.flush()
    said = "Leave 15 minutes earlier for siatkowka"  # whisper drops the Polish letters
    assert match(session, said, NOW, CONFIG) == (
        "OK, you'll leave 15 minutes earlier for Siatkówka from now on."
    )
    assert match(session, "I want to leave half an hour earlier", NOW, CONFIG).startswith(
        "OK, you'll leave 30 minutes earlier for Siatkówka"  # no name: the next event
    )
    assert [(r.title, r.minutes) for r in mpk.early_rules(session)] == [("Siatkówka", 30)]
    assert match(session, "Leave 5 minutes earlier for the dentist", NOW, CONFIG) == (
        "I couldn't find the dentist in your calendar."
    )
    assert match(session, "Stop leaving early for Siatkówka", NOW, CONFIG) == (
        "OK, you'll arrive on time for Siatkówka again."
    )
    assert mpk.early_rules(session) == []

    assert reply_early(session, NOW, CONFIG, ["Siatkówka", "10"]).endswith(
        "for Siatkówka from now on."
    )
    assert reply_early(session, NOW, CONFIG, []) == "Siatkówka: 10 min early"
    assert reply_early(session, NOW, CONFIG, ["0"]) == "Siatkówka: arriving on time again."
    assert reply_early(session, NOW, CONFIG, ["soon"]).startswith("Usage")
