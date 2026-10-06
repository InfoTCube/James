from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class JobOffer(Base):
    """An offer that matched the `jobs` config when first seen (others aren't stored)."""

    __tablename__ = "jobs_offers"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)  # "<source>:<board's id>"
    source: Mapped[str] = mapped_column(String(20))
    title: Mapped[str]
    company: Mapped[str]
    url: Mapped[str]
    salary: Mapped[str | None]  # display text, e.g. "12 000–15 000 PLN/month"
    seniority: Mapped[str]  # as the board says, e.g. "junior" or "Młodszy specjalista (junior)"
    skills: Mapped[str]  # comma-separated
    cities: Mapped[str]  # comma-separated
    remote: Mapped[bool]  # remote work possible
    hybrid: Mapped[bool]
    office_days: Mapped[int | None]  # per week, when hybrid and the board says
    full_time: Mapped[bool | None]  # True: full-time only, False: part-time possible, None: ?
    dedupe_key: Mapped[str] = mapped_column(String(300), index=True)  # service.dedupe_key
    published: Mapped[datetime] = mapped_column(UTCDateTime)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
