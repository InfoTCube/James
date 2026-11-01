"""Reading stored film suggestions. No network here."""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.config import fold
from assistant.modules.films.models import Film, FilmWatched

PICKS = 3


def title_key(title: str) -> str:
    """What two spellings of a title have in common: "Boże Ciało " → "boze cialo"."""
    return fold(" ".join(title.split()))


def mark_watched(session: Session, title: str, now: datetime) -> None:
    """Remember a film as watched (marking it twice is fine)."""
    title = " ".join(title.split())
    session.merge(FilmWatched(key=title_key(title), title=title, marked_at=now))


def unmark_watched(session: Session, title: str) -> bool:
    """Forget a film as watched; False if it wasn't marked."""
    row = session.get(FilmWatched, title_key(title))
    if row is None:
        return False
    session.delete(row)
    return True


def watched(session: Session) -> list[FilmWatched]:
    """Films marked as watched, newest first."""
    return list(session.scalars(select(FilmWatched).order_by(FilmWatched.marked_at.desc())))


def week_of(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


def candidates(session: Session, week: date) -> list[Film]:
    """That week's films, best rated first."""
    q = select(Film).where(Film.week == week).order_by(Film.rating.desc(), Film.id)
    return list(session.scalars(q))


def picked_ids(session: Session) -> set[int]:
    """Every film ever suggested by the AI: not to be suggested again."""
    return set(session.scalars(select(Film.id).where(Film.picked)))


def suggestions(session: Session) -> list[Film]:
    """The newest week's AI picks; without any (AI failed or not run yet, or you've watched them
    all), its best rated. Never a film you've marked as watched."""
    week = session.scalar(select(func.max(Film.week)))
    if week is None:
        return []
    seen = {w.key for w in watched(session)}
    films = [f for f in candidates(session, week) if title_key(f.title) not in seen]
    return [f for f in films if f.picked] or films[:PICKS]


def format_film(f: Film) -> str:
    """Title line, the reason if the AI gave one, and the link."""
    year = f" ({f.year})" if f.year else ""
    lines = [f"🎬 {f.title}{year} · {f.rating:.1f} · {f.providers}"]
    if f.why:
        lines.append(f.why)
    return "\n".join([*lines, f.link])
