from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class Parcel(Base):
    """An InPost parcel being tracked (added with /track)."""

    __tablename__ = "parcels_parcels"

    number: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]  # your name for it ("" = none), e.g. "headphones"
    added_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str | None]  # InPost status name, e.g. "ready_to_pickup"; None: not found yet
    title: Mapped[str | None]  # the status as InPost words it, e.g. "Ready for collection"
    status_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locker: Mapped[str | None]  # "WRO118M, Kościuszki 70 (Przy sklepie Żabka)"
