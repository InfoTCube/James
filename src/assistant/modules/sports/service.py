"""Reading stored matches. No network here."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.modules.sports.models import SportsMatch, SportsTeam

AHEAD = timedelta(days=14)
BACK = timedelta(days=7)
ICONS = {"Soccer": "⚽", "Volleyball": "🏐"}


def upcoming(session: Session, now: datetime) -> list[SportsMatch]:
    """Matches in the next 14 days, soonest first."""
    q = (
        select(SportsMatch)
        .where(SportsMatch.starts >= now, SportsMatch.starts < now + AHEAD)
        .where(SportsMatch.home_score.is_(None))
        .order_by(SportsMatch.starts)
    )
    return list(session.scalars(q))


def results(session: Session, now: datetime) -> list[SportsMatch]:
    """Scores from the last 7 days, newest first."""
    q = (
        select(SportsMatch)
        .where(SportsMatch.starts >= now - BACK, SportsMatch.home_score.is_not(None))
        .order_by(SportsMatch.starts.desc())
    )
    return list(session.scalars(q))


def followed_names(session: Session, teams: Iterable[str]) -> set[str]:
    """The spelling in match data of the teams you follow (config names may differ)."""
    q = select(SportsTeam.title).where(SportsTeam.name.in_(list(teams)))
    return set(session.scalars(q))


def is_followed(m: SportsMatch, names: set[str]) -> bool:
    """One of your teams plays, rather than the match being here only for its league."""
    return m.home in names or m.away in names


def format_match(m: SportsMatch, tz: ZoneInfo) -> str:
    """One line: the score if it's been played, otherwise the kick-off time."""
    icon = ICONS.get(m.sport, "🏆")
    start = m.starts.astimezone(tz)
    if m.home_score is None:
        return f"{icon} {start:%a %d.%m %H:%M} {m.home} vs {m.away} · {m.league}"
    return f"{icon} {start:%d.%m} {m.home} {m.home_score}:{m.away_score} {m.away} · {m.league}"
