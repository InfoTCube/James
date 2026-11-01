"""Reading stored book suggestions. No network here."""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.config import fold
from assistant.modules.books.models import Book, BookRead

PICKS = 3


def title_key(title: str) -> str:
    """What two spellings of a title have in common: "Atomowe  Nawyki" → "atomowe nawyki"."""
    return fold(" ".join(title.split()))


def mark_read(session: Session, title: str, now: datetime) -> None:
    """Remember a book as read (marking it twice is fine)."""
    title = " ".join(title.split())
    session.merge(BookRead(key=title_key(title), title=title, marked_at=now))


def unmark_read(session: Session, title: str) -> bool:
    """Forget a book as read; False if it wasn't marked."""
    row = session.get(BookRead, title_key(title))
    if row is None:
        return False
    session.delete(row)
    return True


def read_books(session: Session) -> list[BookRead]:
    """Books marked as read, newest first."""
    return list(session.scalars(select(BookRead).order_by(BookRead.marked_at.desc())))


def month_of(day: date) -> date:
    """The first day of `day`'s month."""
    return day.replace(day=1)


def candidates(session: Session, month: date) -> list[Book]:
    """That month's books, best rated first."""
    q = select(Book).where(Book.month == month).order_by(Book.rating.desc(), Book.id)
    return list(session.scalars(q))


def picked_ids(session: Session) -> set[str]:
    """Every book ever suggested by the AI: not to be suggested again."""
    return set(session.scalars(select(Book.id).where(Book.picked)))


def suggestions(session: Session) -> list[Book]:
    """The newest month's AI picks; without any (AI failed or not run yet, or you've read them
    all), its best rated. Never a book you've marked as read."""
    month = session.scalar(select(func.max(Book.month)))
    if month is None:
        return []
    seen = {r.key for r in read_books(session)}
    books = [b for b in candidates(session, month) if title_key(b.title) not in seen]
    return [b for b in books if b.picked] or books[:PICKS]


def format_book(b: Book) -> str:
    """Title line, the reason if the AI gave one, and the link."""
    year = f" ({b.year})" if b.year else ""
    lines = [f"📚 {b.title} by {b.author}{year} · {b.rating:.1f}"]
    if b.why:
        lines.append(b.why)
    return "\n".join([*lines, b.link])
