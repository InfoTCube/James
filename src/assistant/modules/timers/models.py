from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class Timer(Base):
    """A kitchen timer, set by voice or Telegram; the dashboard rings it."""

    __tablename__ = "timers_timers"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]  # "" = none, e.g. "pasta"
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    stopped: Mapped[bool] = mapped_column(default=False)  # stopped ringing, or cancelled
