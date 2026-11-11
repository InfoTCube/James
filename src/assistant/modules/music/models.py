from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base


class MusicTrack(Base):
    """The play queue: the lowest id is playing now, the rest come next."""

    __tablename__ = "music_queue"
    __table_args__ = {"sqlite_autoincrement": True}  # a new queue never reuses an old id

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str]
    title: Mapped[str]
    paused: Mapped[bool] = mapped_column(default=False)
