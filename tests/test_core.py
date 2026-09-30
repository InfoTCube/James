from datetime import timedelta
from pathlib import Path

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from assistant.core.collector import is_stale, run_collector
from assistant.core.config import Config, load_config
from assistant.core.db import CollectorRun, make_engine


class FakeCollector:
    schedule = IntervalTrigger(hours=1)

    def __init__(self, name: str, fail: bool = False):
        self.name, self.fail = name, fail

    def run(self, session: Session) -> None:
        session.execute(text("CREATE TABLE IF NOT EXISTS t (x INT)"))
        session.execute(text("INSERT INTO t VALUES (1)"))
        if self.fail:
            raise RuntimeError("boom")


def test_repo_config_is_valid():
    assert load_config(Path("config/config.yaml")).timezone == "Europe/Warsaw"


def test_config_rejects_bad_timezone_and_unknown_keys():
    loc = {"latitude": 0, "longitude": 0}
    with pytest.raises(ValidationError):
        Config(timezone="Mars/Olympus", location=loc)
    with pytest.raises(ValidationError):
        Config(location=loc, typo_key=1)


def test_run_collector_records_runs_and_never_raises(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    assert is_stale(engine, "ok", timedelta(hours=1))

    assert run_collector(FakeCollector("ok"), engine) is True
    assert run_collector(FakeCollector("bad", fail=True), engine) is False

    with Session(engine) as s:
        runs = {r.module: r for r in s.scalars(select(CollectorRun))}
        # failed run rolled back its writes: only the ok run's row exists
        assert s.scalar(text("SELECT count(*) FROM t")) == 1
        assert s.scalar(text("PRAGMA journal_mode")) == "wal"
    assert runs["ok"].status == "ok" and runs["ok"].finished_at.tzinfo is not None
    assert runs["bad"].status == "error" and "boom" in runs["bad"].error
    assert not is_stale(engine, "ok", timedelta(hours=1))
    assert is_stale(engine, "bad", timedelta(hours=1))
