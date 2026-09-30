from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class CalendarEvent(Base):
    """One occurrence (recurring events arrive expanded). Replaced wholesale on every run."""

    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Google instance id
    start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    end: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str]
    location: Mapped[str | None]
    all_day: Mapped[bool]
