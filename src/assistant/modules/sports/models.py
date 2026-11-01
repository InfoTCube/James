from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class SportsTeam(Base):
    """A team from config, once found in TheSportsDB (saves a search on every run)."""

    __tablename__ = "sports_teams"

    name: Mapped[str] = mapped_column(primary_key=True)  # as written in config
    team_id: Mapped[str] = mapped_column(String(20))
    title: Mapped[str]  # TheSportsDB's spelling


class SportsMatch(Base):
    """A match of one of your teams, kept a month. No score yet = not played."""

    __tablename__ = "sports_matches"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # TheSportsDB event id
    team: Mapped[str]  # the config name it was fetched for
    sport: Mapped[str]  # Soccer, Volleyball
    league: Mapped[str]
    home: Mapped[str]
    away: Mapped[str]
    starts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
