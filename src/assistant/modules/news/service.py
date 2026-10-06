"""Reading stored headlines and digests. No network here."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.modules.news.models import NewsDigest, NewsItem

PER_FEED = 25  # newest headlines per feed in the digest: keeps the AI input small


def digest(session: Session, day: date) -> NewsDigest | None:
    """The digest for `day` (made that night), if there is one."""
    return session.get(NewsDigest, day)


def bullets(d: NewsDigest) -> list[str]:
    """The digest's points, without the "• "."""
    return [line.lstrip("•-* ").strip() for line in d.text.splitlines() if line.strip()]


def headlines(session: Session, since: datetime, per_feed: int | None = None) -> list[NewsItem]:
    """The newest `per_feed` (default PER_FEED) headlines of each feed published after `since`,
    newest first."""
    per_feed = per_feed or PER_FEED
    items = session.scalars(
        select(NewsItem).where(NewsItem.published >= since).order_by(NewsItem.published.desc())
    )
    count: dict[str, int] = {}
    kept = []
    for item in items:
        count[item.feed] = count.get(item.feed, 0) + 1
        if count[item.feed] <= per_feed:
            kept.append(item)
    return kept


def latest(session: Session, now: datetime, limit: int = 8) -> list[NewsItem]:
    """The newest headlines from the last day, at most two per feed so one feed can't fill it."""
    return sorted(headlines(session, now - timedelta(days=1), per_feed=2),
                  key=lambda i: i.published, reverse=True)[:limit]  # fmt: skip


def briefing_lines(session: Session, day: date, count: int = 3) -> list[str]:
    """The top `count` points of the day's digest, for the morning briefing ([] without one)."""
    d = digest(session, day)
    return bullets(d)[:count] if d else []
