from datetime import date, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class Event(Base):
    """An event that matched the `events` config when first seen (others aren't stored)."""

    __tablename__ = "events_events"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)  # "<source>:<source's id>"
    source: Mapped[str] = mapped_column(String(20))
    title: Mapped[str]
    url: Mapped[str]
    kind: Mapped[str]  # as the source says: "Spotkanie", "Konferencja", "hackathon", "meetup"...
    topics: Mapped[str]  # comma-separated
    city: Mapped[str]  # where it happens, as the source says; "" when online only
    online: Mapped[bool]  # can be attended online
    starts: Mapped[date]  # local (Warsaw) dates
    ends: Mapped[date]  # = starts for one-day events
    at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # start time, when the source says
    free: Mapped[bool | None]
    dedupe_key: Mapped[str] = mapped_column(String(300), index=True)  # service.dedupe_key
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
