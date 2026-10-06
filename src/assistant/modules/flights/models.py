from datetime import date, datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class FlightWatch(Base):
    """A trip to look for, set from Telegram (/flight_add)."""

    __tablename__ = "flights_watches"
    __table_args__ = {"sqlite_autoincrement": True}  # ids shown in /flights are never reused

    id: Mapped[int] = mapped_column(primary_key=True)
    destinations: Mapped[str]  # IATA codes, comma-separated; "" = anywhere
    first_day: Mapped[date]  # the whole trip is between these days (local dates)
    last_day: Mapped[date]
    nights_min: Mapped[int | None]  # None = one way
    nights_max: Mapped[int | None]
    max_price: Mapped[int]  # PLN, one person, there and back together
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # None: check soon


class FlightFare(Base):
    """The cheapest trip found for a watch, per airline and destination, at the last check."""

    __tablename__ = "flights_fares"

    watch_id: Mapped[int] = mapped_column(primary_key=True)
    airline: Mapped[str] = mapped_column(primary_key=True)  # "Ryanair" | "Wizz Air"
    destination: Mapped[str] = mapped_column(primary_key=True)  # IATA code
    city: Mapped[str]
    out_day: Mapped[date]
    back_day: Mapped[date | None]  # None = one way
    price: Mapped[float]  # PLN
    url: Mapped[str]
    alerted_price: Mapped[float | None]  # the price last sent to Telegram
