from datetime import date, datetime

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class FilmWatched(Base):
    """A film you've marked as watched: never suggested, and shown to the AI as your taste."""

    __tablename__ = "films_watched"

    key: Mapped[str] = mapped_column(primary_key=True)  # title, case and diacritics folded
    title: Mapped[str]
    marked_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Film(Base):
    """A candidate for one week's suggestions. Picked films are kept, so they aren't suggested
    again; the rest are replaced by next week's candidates."""

    __tablename__ = "films"

    id: Mapped[int] = mapped_column(primary_key=True)  # JustWatch object id
    week: Mapped[date] = mapped_column(index=True)  # its Monday (local)
    title: Mapped[str]
    year: Mapped[int | None]
    overview: Mapped[str] = mapped_column(Text)
    rating: Mapped[float]
    providers: Mapped[str]  # streaming services in Poland, comma-separated
    link: Mapped[str]
    picked: Mapped[bool] = mapped_column(default=False)
    why: Mapped[str | None]  # the AI's one-sentence reason
