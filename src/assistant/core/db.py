"""Shared SQLite database (WAL mode) and the collector_runs table."""

import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import DateTime, Engine, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

DB_PATH = Path(os.environ.get("ASSISTANT_DB", "data/assistant.db"))


class UTCDateTime(TypeDecorator):
    """Stores aware datetimes as UTC; SQLite drops tzinfo, so re-attach it on load."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use aware datetimes")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=UTC) if value else None


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CollectorRun(Base):
    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(10))  # "ok" | "error"
    error: Mapped[str | None] = mapped_column(Text)


def make_engine(path: Path) -> Engine:
    """Create an engine with WAL + foreign keys, and create all tables."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    import assistant.modules  # noqa: F401  registers module tables on Base.metadata

    Base.metadata.create_all(engine)
    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(DB_PATH)
