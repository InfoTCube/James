from datetime import date, datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class MpkFeed(Base):
    """The GTFS file currently imported (one row)."""

    __tablename__ = "mpk_feed"

    file_id: Mapped[int] = mapped_column(primary_key=True)  # open data portal file id
    name: Mapped[str]
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime)


class MpkStop(Base):
    __tablename__ = "mpk_stops"

    stop_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(index=True)
    lat: Mapped[float]
    lon: Mapped[float]


class MpkRoute(Base):
    __tablename__ = "mpk_routes"

    route_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    short_name: Mapped[str]
    route_type: Mapped[int]  # 0 tram, 3 bus


class MpkTrip(Base):
    __tablename__ = "mpk_trips"

    trip_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(20))
    service_id: Mapped[str] = mapped_column(String(20), index=True)
    headsign: Mapped[str]


class MpkStopTime(Base):
    __tablename__ = "mpk_stop_times"
    __table_args__ = (Index("ix_mpk_stop_times_stop_dep", "stop_id", "dep"),)

    trip_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seq: Mapped[int] = mapped_column(primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(20))
    arr: Mapped[int]  # seconds after local midnight of the service day (can exceed 24 h)
    dep: Mapped[int]


class MpkServiceDate(Base):
    """calendar.txt + calendar_dates.txt expanded: service_id runs on date."""

    __tablename__ = "mpk_service_dates"

    service_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
