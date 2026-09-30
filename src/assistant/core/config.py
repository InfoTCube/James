"""Load and validate config/config.yaml."""

import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

CONFIG_PATH = Path(os.environ.get("ASSISTANT_CONFIG", "config/config.yaml"))


class Location(BaseModel):
    latitude: float
    longitude: float


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Warsaw"
    location: Location

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
