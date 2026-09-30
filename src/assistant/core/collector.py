"""Collector contract and the runner that records every run in collector_runs."""

import logging
from datetime import datetime, timedelta
from typing import Protocol

from apscheduler.triggers.base import BaseTrigger
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from assistant.core.db import CollectorRun, utcnow

log = logging.getLogger(__name__)

KEEP_RUNS = timedelta(days=30)  # older collector_runs rows are deleted


class Collector(Protocol):
    name: str
    schedule: BaseTrigger  # e.g. IntervalTrigger(hours=1) or CronTrigger(hour=6)

    def run(self, session: Session) -> None:
        """Fetch and store data. Must be idempotent. Committed only if it doesn't raise."""


def run_collector(collector: Collector, engine: Engine) -> bool:
    """Run one collector in its own transaction and record the outcome. Never raises."""
    started, error = utcnow(), None
    try:
        with Session(engine) as session, session.begin():
            collector.run(session)
    except Exception as e:
        log.exception("collector %s failed", collector.name)
        error = f"{type(e).__name__}: {e}"
    with Session(engine) as session, session.begin():
        session.execute(delete(CollectorRun).where(CollectorRun.finished_at < started - KEEP_RUNS))
        session.add(
            CollectorRun(
                module=collector.name,
                started_at=started,
                finished_at=utcnow(),
                status="error" if error else "ok",
                error=error,
            )
        )
    log.info("collector %s: %s", collector.name, "error" if error else "ok")
    return error is None


def last_success(engine: Engine, module: str) -> datetime | None:
    """When the module's collector last finished OK (None if never)."""
    with Session(engine) as session:
        return session.scalar(
            select(func.max(CollectorRun.finished_at)).where(
                CollectorRun.module == module, CollectorRun.status == "ok"
            )
        )


def is_stale(engine: Engine, module: str, max_age: timedelta) -> bool:
    """True if the module has no successful run within max_age — show its data as stale."""
    ok = last_success(engine, module)
    return ok is None or utcnow() - ok > max_age
