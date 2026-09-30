from datetime import date, datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class Alarm(Base):
    """One alarm. Automatic ones (source="auto") are re-planned from the calendar; manual ones
    come from the bot (later: voice). A cancelled or rung alarm is never touched again."""

    __tablename__ = "alarm_alarms"
    __table_args__ = {"sqlite_autoincrement": True}  # ids shown in /alarms are never reused

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    label: Mapped[str | None]
    source: Mapped[str]  # "auto" | "manual"
    day: Mapped[date | None]  # local day an automatic alarm is for (one per day)
    cancelled: Mapped[bool] = mapped_column(default=False)
    rung_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # started ringing (this round)
    snoozes: Mapped[int] = mapped_column(default=0)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # stopped or timed out
