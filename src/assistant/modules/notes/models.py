from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class Note(Base):
    __tablename__ = "notes_notes"
    __table_args__ = {"sqlite_autoincrement": True}  # ids shown in /notes are never reused

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str]
    folded: Mapped[str]  # core.config.fold(text): lowercase, no diacritics, for search
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
