from datetime import date, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class BookRead(Base):
    """A book you've marked as read: never suggested, and shown to the AI as your taste."""

    __tablename__ = "books_read"

    key: Mapped[str] = mapped_column(primary_key=True)  # title, case and diacritics folded
    title: Mapped[str]
    marked_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Book(Base):
    """A candidate for one month's suggestions. Picked books are kept, so they aren't suggested
    again; the rest are replaced by next month's candidates."""

    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)  # Open Library work id
    month: Mapped[date] = mapped_column(index=True)  # its first day
    title: Mapped[str]
    author: Mapped[str]
    year: Mapped[int | None]
    rating: Mapped[float]
    link: Mapped[str]
    picked: Mapped[bool] = mapped_column(default=False)
    why: Mapped[str | None]  # the AI's one-sentence reason
