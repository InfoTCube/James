"""Spoken request (text) → reply text. No audio here: the mic and speaker are in __main__.

Rules first (regexes over the transcript, reusing the bot's replies); anything they don't match
goes to Claude (`ai.ask(..., anytime=True)`), and "Sorry" if that fails too.
"""

import difflib
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import NamedTuple

from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.config import Config, fold
from assistant.core.db import get_engine
from assistant.modules.alarm import actions
from assistant.modules.alarm import service as alarm
from assistant.modules.calendar import client as calendar_client
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.alerts import format_connection
from assistant.modules.news import service as news
from assistant.modules.sports import service as sports
from assistant.services.bot import replies

log = logging.getLogger(__name__)

Handler = Callable[[Session, datetime, Config, re.Match], str]

AI_PROMPT = (
    "The input is a spoken request to a home assistant that its commands didn't cover. Answer in "
    "at most two short sentences, plain text to be read aloud. If it needs the user's own data "
    "(calendar, alarms, transport, notes) or an action, say you can't do that by voice yet."
)
SORRY = "Sorry, I didn't get that."
NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}  # fmt: skip
# "7", "7.30", "7 30", "seven", "7:30 p.m.", "7 o'clock" (whisper writes times either way)
# (ends at a word break, or right before "pm": "5pm")
TIME = (r"\b(\d{1,2}|" + "|".join(NUMBERS) + r")(?:[:. ](\d{2}))?(?=\W|$|[ap]\.?\s*m\b)\s*"
        r"(?:o'?clock\b\s*)?(?:([ap])\.?\s*m\b\.?)?")  # fmt: skip
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
MONTHS = ("january february march april may june july august september october november "
          "december").split()  # fmt: skip


def _clock(m: re.Match) -> tuple[int, int, bool]:
    """A TIME match → (hour 0–23, minute, said a.m./p.m.)."""
    word = m[1].lower()
    hour = NUMBERS[word] if word in NUMBERS else int(word)
    if m[3]:
        hour = hour % 12 + (12 if m[3].lower() == "p" else 0)
    return hour, int(m[2] or 0), bool(m[3])


def alarm_time(text: str) -> str | None:
    """ "7.30 a.m." / "7 30" / "seven" / "7:30 pm" → "7:30" / "19:30" (24 h, for parse_time)."""
    m = re.search(TIME, text, re.IGNORECASE)
    if not m:
        return None
    hour, minute, _ = _clock(m)
    return f"{hour}:{minute:02d}"


def _take(pattern: str, text: str) -> tuple[re.Match | None, str]:
    """The first match of `pattern` and `text` without it."""
    m = re.search(pattern, text, re.IGNORECASE)
    return (m, f"{text[: m.start()]} {text[m.end() :]}") if m else (None, text)


def _day(text: str, today: date) -> tuple[date | None, str]:
    """ "tomorrow", "on Friday", "12th of October", "October 12" → the date, and `text` without it.
    Raises ValueError for a date that doesn't exist."""
    if (m := _take(r"\b(on )?(the )?day after tomorrow\b", text))[0]:
        return today + timedelta(days=2), m[1]
    if (m := _take(r"\b(today|tonight|this evening)\b", text))[0]:
        return today, m[1]
    if (m := _take(r"\btomorrow\b", text))[0]:
        return today + timedelta(days=1), m[1]
    m, rest = _take(rf"\b(on |next |this )?({'|'.join(WEEKDAYS)})\b", text)
    if m:  # the coming one: "Tuesday" said on a Tuesday is next week
        ahead = (WEEKDAYS.index(m[2].lower()) - today.weekday() - 1) % 7 + 1
        return today + timedelta(days=ahead), rest
    months = "|".join(MONTHS)
    m, rest = _take(rf"\b(?:on )?(?:the )?(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({months})\b", text)
    day, month = (m[1], m[2]) if m else (None, None)
    if not m:
        m, rest = _take(rf"\b(?:on )?({months}) (?:the )?(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
        day, month = (m[2], m[1]) if m else (None, None)
    if not m:
        return None, text
    d = date(today.year, MONTHS.index(month.lower()) + 1, int(day))
    return (d if d >= today else d.replace(year=today.year + 1)), rest


class NewEvent(NamedTuple):
    title: str
    start: datetime | date | None  # a date = all day; None = no day or time said
    end: datetime | date | None
    location: str | None


def parse_event(text: str, now: datetime, config: Config) -> NewEvent:
    """ "Add dentist to my calendar tomorrow at 3 pm for 2 hours" → title, start, end; "at uni"
    (a place alias) → location. 1 h unless said; no time → all day; no day → the next such
    time. Raises ValueError for a date that doesn't exist."""
    tz, today = config.tz, now.astimezone(config.tz).date()
    m, text = _take(r"\bfor (an?|half an|\d+|" + "|".join(NUMBERS) + r") (hour|minute|min)s?\b",
                    text)  # fmt: skip
    minutes = 60
    if m:
        n = {"a": 1, "an": 1, "half an": 0.5, **NUMBERS}.get(m[1].lower()) or int(m[1])
        minutes = round(n * (60 if m[2].lower() == "hour" else 1))
    location = None
    if config.places:
        aliases = "|".join(map(re.escape, config.places))
        place, text = _take(rf"\b(?:at|in) (?:the )?({aliases})\b", text)
        location = place[1].lower() if place else None
    clock, text = _take(rf"\bat {TIME}", text)
    day, text = _day(text, today)

    title = re.sub(r"^\s*(please )?(add|put|create|schedule|make|new)( me)?\b", "", text,
                   flags=re.I)  # fmt: skip
    title = re.sub(
        r"\b(to|in|on|into) (my |the )?calendar\b|\b(an?|new) (new )?(calendar )?event\b"
        r"( called| named)?|\bcalled\b",
        " ", title, flags=re.I,
    )  # fmt: skip
    title = re.sub(r"^(a|an|the) ", "", re.sub(r"\s+", " ", title).strip(" ,.:"), flags=re.I)
    title = title[:1].upper() + title[1:]

    if clock is None:
        return NewEvent(title, day, day and day + timedelta(days=1), location)
    hour, minute, said_ampm = _clock(clock)
    if not said_ampm and 1 <= hour <= 7:
        hour += 12  # ponytail: "at 3" is 15:00; nobody books a 3 a.m. meeting by voice
    start = datetime.combine(day or today, time(hour, minute), tz)
    if day is None and start <= now:
        start += timedelta(days=1)
    return NewEvent(title, start, start + timedelta(minutes=minutes), location)


def _reply(fn) -> Handler:
    return lambda session, now, config, m: fn(session, now, config, [])


def _set_alarm(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    text = alarm_time(m["rest"])
    at = alarm.parse_time(text, now, config.tz) if text else None
    if at is None:
        return "At what time? Say for example: wake me up at 7:30."
    alarm.add_alarm(session, at)
    local = at.astimezone(config.tz)
    day = "today" if local.date() == now.astimezone(config.tz).date() else "tomorrow"
    return f"Alarm set for {local:%H:%M} {day}."


def _cancel_alarm(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    if ringing := alarm.ringing_alarm(session, now):  # "turn off the alarm" while it rings
        actions.stop(session, ringing, now, config)
        return "OK."
    alarms = alarm.upcoming_alarms(session, now)
    if re.search(r"\ball\b", m["rest"], re.IGNORECASE):
        for a in alarms:
            alarm.cancel_alarm(session, a.id)
        n = len(alarms)
        return f"Cancelled {n} alarm{'s' * (n != 1)}." if alarms else "No alarms set."
    if t := re.search(TIME, m["rest"], re.IGNORECASE):
        hour, minute, said_ampm = _clock(t)
        mod = 24 if said_ampm else 12  # plain "7" matches 7:00 and 19:00: asked which below
        local = [(a, a.at.astimezone(config.tz)) for a in alarms]
        alarms = [a for a, t in local if (t.hour % mod, t.minute) == (hour % mod, minute)]
    times = [f"{a.at.astimezone(config.tz):%H:%M}" for a in alarms]
    if not alarms:
        return f"No alarm at {hour}:{minute:02d}." if t else "No alarms set."
    if len(alarms) > 1:
        return f"Which one? You have alarms at {', '.join(times[:-1])} and {times[-1]}."
    alarm.cancel_alarm(session, alarms[0].id)
    return f"Cancelled the {times[0]} alarm."


def _when(start: datetime | date, now: datetime, config: Config) -> str:
    """ "today at 15:00", "tomorrow", "Friday 9 October at 10:00"."""
    timed = isinstance(start, datetime)
    local = start.astimezone(config.tz) if timed else start
    d = local.date() if timed else local
    ahead = (d - now.astimezone(config.tz).date()).days
    day = {0: "today", 1: "tomorrow"}.get(ahead, f"{d:%A} {d.day} {d:%B}")
    return f"{day} at {local:%H:%M}" if timed else day


def _add_event(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    try:
        event = parse_event(m.string, now, config)
    except ValueError:
        return "Sorry, I didn't get the date."
    example = "Say for example: add dentist to my calendar tomorrow at 3 pm."
    if not event.title:
        return f"What should I add? {example}"
    if event.start is None:
        return f"When? {example}"
    try:
        calendar_client.add_event(event.title, event.start, event.end, event.location)
    except Exception:  # no Google credentials, network down...
        log.exception("adding a calendar event failed")
        return "Sorry, I couldn't reach Google Calendar."
    where = f" at {event.location}" if event.location else ""
    return f"Added {event.title}{where}, {_when(event.start, now, config)}."


def _alarm_action(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    ringing = alarm.ringing_alarm(session, now)
    if ringing is None:
        return "Nothing is ringing."
    if m[1].lower() == "snooze":
        actions.snooze(session, ringing, now, config)
    else:
        actions.stop(session, ringing, now, config)  # the dashboard reads the briefing
    return "OK."


def _note(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    return replies.reply_note(session, now, config, [m["text"]])


def _time(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    local = now.astimezone(config.tz)
    return f"It's {local:%H:%M}, {local:%A} {local.day} {local:%B}."


def _go_out(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    """When to leave home next (today, or tomorrow if said), like the morning briefing."""
    tz = config.tz
    tomorrow = re.search(r"\btomorrow\b", m.string, re.IGNORECASE) is not None
    day = now.astimezone(tz).date() + timedelta(days=tomorrow)
    d = mpk.first_departure(session, day, config, after=None if tomorrow else now)
    if d is None:
        return f"You don't need to go out {'tomorrow' if tomorrow else 'again today'}."
    when = f"{'tomorrow ' if tomorrow else ''}at {d.leave_by.astimezone(tz):%H:%M}"
    lines = [f"Leave {when} for {d.leg.title}, at {d.leg.arrive_by.astimezone(tz):%H:%M}."]
    if d.connection:
        lines.append(format_connection(d.connection, tz))
    return "\n".join(lines)


AMOUNTS = {**NUMBERS, "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30,
           "forty": 40, "forty-five": 45, "a": 1, "an": 1, "half an": 0.5}  # fmt: skip


def _event_title(session: Session, now: datetime, config: Config, said: str | None) -> str | None:
    """The calendar title of an upcoming event: the one `said` names (whisper writes Polish names
    as it hears them, so loosely), or with nothing said the next event with a location."""
    if not said:
        event = calendar.next_event_with_location(session, now, config.tz)
        return event.title if event else None
    events = calendar.events_between(session, now, now + timedelta(days=14), config.tz)
    titles = {fold(e.title): e.title for e in events if not e.all_day}
    said = fold(re.sub(r"^(the|my) ", "", said.strip(" .,!?"), flags=re.IGNORECASE))
    if found := [t for key, t in titles.items() if said in key]:
        return found[0]
    close = difflib.get_close_matches(said, titles, n=1, cutoff=0.5)
    return titles[close[0]] if close else None


def _leave_earlier(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    n = m["n"].lower()
    amount = AMOUNTS[n] if n in AMOUNTS else int(n)
    minutes = round(amount * (60 if m["unit"].lower().startswith("hour") else 1))
    title = _event_title(session, now, config, m["title"])
    if title is None:
        return f"I couldn't find {m['title']} in your calendar." if m["title"] else (
            "You have no upcoming event with a location.")  # fmt: skip
    title = mpk.set_early(session, title, min(minutes, mpk.MAX_EARLY))
    return f"OK, you'll leave {minutes} minutes earlier for {title} from now on."


def _arrive_on_time(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    said = m["title"] or m["title2"]
    title = _event_title(session, now, config, said)
    if title is None:
        return f"I couldn't find {said} in your calendar." if said else (
            "You have no upcoming event with a location.")  # fmt: skip
    return f"OK, you'll arrive on time for {mpk.set_early(session, title, 0)} again."


def _agenda(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    """The calendar for the day said ("tomorrow", "on Friday", "on 12 October"), else today."""
    today = now.astimezone(config.tz).date()
    try:
        day = _day(m.string, today)[0] or today
    except ValueError:  # "31 February"
        day = today
    ahead = (day - today).days
    label = {0: "today", 1: "tomorrow"}.get(ahead, f"on {day:%A} {day.day} {day:%B}")
    return replies.reply_day(session, day, config, label)


def _news(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    d = news.digest(session, now.astimezone(config.tz).date())
    if d:
        return "\n".join(news.bullets(d))
    return "\n".join(i.title for i in news.latest(session, now, limit=5)) or "No news yet."


def _sports(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    """Your teams' next matches and latest scores: a whole league is too long to read out."""
    names = sports.followed_names(session, config.sports.teams)

    def mine(matches: list) -> list:
        return [x for x in matches if sports.is_followed(x, names)]

    shown = mine(sports.upcoming(session, now))[:4] + mine(sports.results(session, now))[:3]
    if not shown:
        return "No matches of your teams coming up, and no recent scores."
    return "\n".join(sports.format_match(x, config.tz) for x in shown)


def _marked_film(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    return replies.reply_watched(session, now, config, [m["title"].strip(" .,!?")])


def _marked_book(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    return replies.reply_read(session, now, config, [m["title"].strip(" .,!?")])


def _help(session: Session, now: datetime, config: Config, m: re.Match) -> str:
    return (
        "Ask me about the weather, your next tram, today's plans, the news, birthdays, jobs, "
        "events, flights, films, books, sports or promotions. Say wake me up at 7:30, cancel the "
        "alarm, take a note, add dentist to my calendar tomorrow at 3 pm, or stop and snooze."
    )


# First match wins, so the specific ones go first.
INTENTS: list[tuple[str, Handler]] = [
    (r"^i(?:'?ve| have)?(?: already)? (?:watched|seen) (?P<title>.+)", _marked_film),
    (r"^i(?:'?ve| have)?(?: already)? read (?P<title>.+)", _marked_book),
    (
        r"\b(cancel|delete|remove|turn off|switch off|disable)\b(?P<rest>.*\balarms?\b.*)",
        _cancel_alarm,
    ),
    (r"^(stop|snooze|i'?m up)( it| that| the alarm| ringing)?$", _alarm_action),
    (r"\b(wake me|alarm (for|at)|set (an |the |my )?alarm)\b(?P<rest>.*)", _set_alarm),
    (r"\balarms?\b", _reply(replies.reply_alarms)),
    (r"^(take a |make a |add a )?note( down)?:?\s+(that )?(?P<text>.+)", _note),
    (r"^(remember|write down) (that |to )?(?P<text>.+)", _note),
    (r"\bnotes\b", _reply(replies.reply_notes)),
    (  # "leave 15 minutes earlier for Siatkówka", "be at uni half an hour early"
        r"\b(?P<n>\d+|" + "|".join(sorted(AMOUNTS, key=len, reverse=True)) + r") "
        r"(?P<unit>minutes?|mins?|hours?) "
        r"(earlier|early|sooner)\b(?: (?:for|to|at) (?P<title>.+))?",
        _leave_earlier,
    ),
    (  # "stop leaving early for Siatkówka", "arrive on time for the meeting"
        r"\b(stop|don'?t|no longer|cancel)\b.*\bearl(y|ier)\b(?: (?:for|to|at) (?P<title>.+))?"
        r"|\b(arrive|be there|leave) on time\b(?: (?:for|to|at) (?P<title2>.+))?",
        _arrive_on_time,
    ),
    (
        r"^(please )?(add|put|create|schedule|make|new)\b"
        r".*\b(calendar|event|appointment|meeting)\b",
        _add_event,
    ),
    (r"^(please )?schedule\b", _add_event),
    (r"\b(go|head|get) out\b|\b(when|what time)\b.*\b(leave|go)\b", _go_out),
    (
        r"\bwhat time is it\b|\bwhat'?s the time\b|\bwhat day is it\b|\bwhat'?s the date\b"
        r"|\btoday'?s date\b",
        _time,
    ),
    (r"\b(briefing|good morning)\b", _reply(replies.reply_briefing)),
    (
        r"\b(weather|wear|jacket|coat|umbrella|rain\w*|cold|warm|temperature|forecast)\b",
        _reply(replies.reply_weather),
    ),
    (r"\b(match|matches|scores?|sports?|football)\b", _sports),  # before "next": not a trip
    (r"\b(promotions?|promos?|biedronka|on sale)\b", _reply(replies.reply_promos)),
    (r"\b(tram|bus|leave|departures?|trip|next)\b", _reply(replies.reply_next)),
    (
        r"\b(today|tomorrow|calendar|schedule|plans?|agenda)\b|\bwhat do i have\b"
        r"|\bdo i have anything\b|\bwhat('s| is) on\b|\bwhat am i doing\b|\bam i (busy|free)\b"
        rf"|\b({'|'.join(WEEKDAYS)})\b",  # a day named and nothing else matched: its calendar
        _agenda,
    ),
    (r"\bbirthdays?\b", _reply(replies.reply_birthdays)),
    (r"\b(news|headlines)\b", _news),
    (r"\b(jobs?|job offers)\b", _jobs := _reply(replies.reply_jobs)),
    (r"\b(events?|meetups?|hackathons?)\b", _events := _reply(replies.reply_events)),
    (r"\b(flights?|fly|plane)\b", _reply(replies.reply_flights)),
    (r"\b(films?|movies?)\b", _films := _reply(replies.reply_films)),
    (
        r"\bbooks\b|\bbook (suggestion|recommendation)s?\b|\b(should i|to) read\b",
        _books := _reply(replies.reply_books),
    ),
    (r"\b(help|what can you do)\b", _help),
]
COMPILED = [(re.compile(p, re.IGNORECASE), h) for p, h in INTENTS]
# replies made of headlines and titles: each line is all English or all Polish, so it's read by
# one voice (tts.synthesize_line); our own sentences switch voice for Polish names instead
ONE_LANGUAGE = {_news, _jobs, _events, _films, _books}


def _find(text: str) -> tuple[Handler, re.Match] | None:
    text = re.sub(r"\s+", " ", text).strip(" .!?")
    for pattern, handler in COMPILED:
        if m := pattern.search(text):
            return handler, m
    return None


def match(session: Session, text: str, now: datetime, config: Config) -> str | None:
    """The reply from the first matching rule, or None if no rule matches."""
    found = _find(text)
    return found[0](session, now, config, found[1]) if found else None


def answer(text: str, now: datetime, config: Config) -> tuple[str, bool]:
    """Reply to a spoken request: a rule, else Claude, else SORRY. Also whether each line of it
    is in one language (headlines, titles: ONE_LANGUAGE)."""
    if found := _find(text):
        handler, m = found
        with Session(get_engine()) as session, session.begin():  # e.g. a new alarm is saved
            return handler(session, now, config, m), handler in ONE_LANGUAGE
    return ai.ask("voice", AI_PROMPT, text, anytime=True) or SORRY, False  # outside a transaction


def spoken(text: str, max_lines: int = 8) -> list[str]:
    """A Telegram-style reply → lines to read aloud: no emoji or links, symbols in words, at most
    `max_lines` lines (a reply like /jobs would go on for minutes)."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s*–\s*(?!\d)", ", ", text)  # "Mateusz – today"; "08:00–16:00" is for tts
    for symbol, word in {"→": " to ", "·": ",", "°": " degrees", "~": "about ",
                         "#": "number ", "…": ""}.items():  # fmt: skip
        text = text.replace(symbol, word)
    # emoji are "other symbols" (So); FE0F/200D glue them together
    text = "".join(c for c in text if c not in "️‍" and unicodedata.category(c) != "So")
    lines = [re.sub(r" +(,|\.(?!\w))", r"\1", re.sub(" +", " ", line)).strip(" ,")
             for line in text.splitlines()]  # fmt: skip
    return [line for line in lines if line][:max_lines]
