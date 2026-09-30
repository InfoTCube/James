"""Load and validate config/config.yaml."""

import os
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


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Warsaw"
    location: Location
    places: dict[str, Place] = Field(default_factory=dict)
    location_rules: list[LocationRule] = []
    alarm: AlarmConfig = Field(default_factory=AlarmConfig)
    voice: str = "en_GB-cori-medium"  # Piper voice for reading aloud (core/tts.py)
    voice_pl: str = "pl_PL-gosia-medium"  # ...and for Polish names in it

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
