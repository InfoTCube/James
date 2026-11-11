"""Load and validate config/config.yaml."""

import os
import re
import unicodedata
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_PATH = Path(os.environ.get("ASSISTANT_CONFIG", "config/config.yaml"))


class Location(BaseModel):
    latitude: float
    longitude: float


Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def fold(text: str) -> str:
    """Lowercase and strip Polish diacritics for matching: "Główny" → "glowny"."""
    return (
        unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
        .encode("ascii", "ignore")
        .decode()
    )


def has_word(text: str, word: str) -> bool:
    """`word` appears in `text` as a whole word ("java" not in "javascript"; "c#" works),
    case and diacritics ignored."""
    return re.search(rf"(?<!\w){re.escape(fold(word))}(?!\w)", fold(text)) is not None


class Place(BaseModel):
    """A place you refer to by alias (home, uni, ...)."""

    model_config = ConfigDict(extra="forbid")

    stops: list[str]  # GTFS stop names (all platforms with that name are used)
    keywords: list[str] = []  # extra words that identify it in calendar locations
    walk_minutes: int = 5  # walk between the place and its stops


class LocationRule(BaseModel):
    """Gives calendar events without a location one, e.g. work → office on office days.
    On other days matching events keep no location (= you don't leave home for them)."""

    model_config = ConfigDict(extra="forbid")

    title: str  # matches events whose title contains this (case/diacritics ignored)
    days: list[Weekday]
    place: str  # alias from `places`


class AlarmConfig(BaseModel):
    """Automatic wake-up alarm, planned from your first trip of the day."""

    model_config = ConfigDict(extra="forbid")

    before_leaving_minutes: int = 40
    latest: time = time(11, 0)  # no automatic alarm at or after this: you're up anyway


class JobsConfig(BaseModel):
    """Which job offers get a Telegram alert. Matching ignores case and Polish diacritics."""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = []  # in the title or skills; also the search terms. Empty = jobs off
    exclude: list[str] = []  # ...but not with one of these in the title
    seniority: list[str] = []  # e.g. [trainee, junior]; empty = any. Offers with no level pass
    cities: list[str] = ["Wrocław"]  # any work mode
    hybrid_cities: list[str] = []  # hybrid only, with at most `max_office_days` in the office
    max_office_days: int = 1  # per week; offers that don't say pass (check them yourself)
    remote: bool = True  # also remote offers from anywhere


class EventsConfig(BaseModel):
    """Which IT events and hackathons get a Telegram alert. Matching ignores case and Polish
    diacritics."""

    model_config = ConfigDict(extra="forbid")

    cities: list[str] = ["Wrocław"]  # all events here match (minus `exclude`); also searched
    online: bool = True  # online events too, but only with one of `keywords`
    keywords: list[str] = []  # in the title, kind or topics, e.g. [.net, hackathon]
    exclude: list[str] = []  # never with one of these in the title, kind or topics


class NewsConfig(BaseModel):
    """RSS/Atom feeds for the news digest (written by AI overnight, raw headlines without it)."""

    model_config = ConfigDict(extra="forbid")

    feeds: dict[str, str] = {}  # name → feed URL; empty = news off


class FilmsConfig(BaseModel):
    """Weekly film suggestions."""

    model_config = ConfigDict(extra="forbid")

    taste: str = ""  # free text for the AI, e.g. "smart sci-fi, slow thrillers, no horror"
    providers: list[str] = []  # streaming services as JustWatch names them; empty = any in Poland
    min_rating: float = 7.0  # IMDb
    min_votes: int = 5000  # IMDb


class MealsConfig(BaseModel):
    """Weekly dinner ideas from the Biedronka offers on your /buy products."""

    model_config = ConfigDict(extra="forbid")

    taste: str = ""  # free text for the AI, e.g. "quick, high protein, no mushrooms"


class BooksConfig(BaseModel):
    """Monthly book suggestions."""

    model_config = ConfigDict(extra="forbid")

    subjects: list[str] = []  # Open Library subjects, e.g. [science fiction]; empty = books off
    taste: str = ""  # free text for the AI
    languages: list[str] = ["eng", "pol"]  # has an edition in one of these; empty = any
    since_year: int = 2000  # first published this year or later
    min_ratings: int = 5  # fewer people rated it: too little to go on


class SportsConfig(BaseModel):
    """Football and volleyball teams and leagues to follow. Both empty = sports off."""

    model_config = ConfigDict(extra="forbid")

    teams: list[str] = []  # as TheSportsDB finds them, e.g. [Śląsk Wrocław]
    leagues: dict[str, int] = {}  # your name → TheSportsDB league id, e.g. {PlusLiga: 5619}


class ListeningConfig(BaseModel):
    """Voice service: wake word and the sound device (mic + speaker)."""

    model_config = ConfigDict(extra="forbid")

    wake_word: str = "hey_jarvis"  # built-in openWakeWord model, or a .onnx next to config.yaml
    wake_threshold: float = 0.5  # 0–1: higher = fewer false wakes, more missed ones
    silence_level: int = 500  # mic loudness (RMS, 0–32767) below which you've stopped talking
    device: str | None = None  # sound device name (or part of it); None = system default


class ScreenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dark_from: time | None = time(23, 0)  # dashboard goes black from here (None: never)
    dark_until: time = time(7, 0)  # ...until here; an alarm, "leave now", voice or a tap wakes it


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Warsaw"
    location: Location
    places: dict[str, Place] = Field(default_factory=dict)
    location_rules: list[LocationRule] = []
    alarm: AlarmConfig = Field(default_factory=AlarmConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    films: FilmsConfig = Field(default_factory=FilmsConfig)
    books: BooksConfig = Field(default_factory=BooksConfig)
    meals: MealsConfig = Field(default_factory=MealsConfig)
    sports: SportsConfig = Field(default_factory=SportsConfig)
    voice: str = "en_GB-alan-medium"  # Piper voice for reading aloud (core/tts.py)
    voice_pl: str = "pl_PL-darkman-medium"  # ...and for Polish names in it
    listening: ListeningConfig = Field(default_factory=ListeningConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)

    @model_validator(mode="after")
    def _rule_places_exist(self) -> "Config":
        for rule in self.location_rules:
            if rule.place not in self.places:
                raise ValueError(f"location rule {rule.title!r}: unknown place {rule.place!r}")
        return self

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def load_config(path: Path = CONFIG_PATH) -> Config:
    """Parse and validate the config file (cached)."""
    return Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
