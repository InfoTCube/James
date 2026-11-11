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


def test_status_reports_failures_ai_calls_and_disk(tmp_path, monkeypatch):
    from assistant.core.db import utcnow
    from assistant.services.bot import replies

    engine = make_engine(tmp_path / "a.db")
    run_collector(FakeCollector("weather"), engine)
    run_collector(FakeCollector("mpk", fail=True), engine)
    monkeypatch.setattr(replies, "free_disk_gb", lambda: 1.5)
    config = Config(location={"latitude": 0, "longitude": 0})
    with Session(engine) as s:
        text_ = replies.reply_status(s, utcnow(), config, [])
    assert text_.splitlines() == [
        "Collectors: 1/2 OK",
        "❌ mpk (never OK): RuntimeError: boom",
        "AI calls in 24 h: 0/30",
        "⚠️ Disk free: 1.5 GB",
    ]


def test_backup_copies_and_keeps_the_newest(tmp_path):
    import sqlite3

    from assistant.modules.backup.collector import backup

    db = tmp_path / "a.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (42)")
    for day in range(1, 5):
        copy = backup(db, tmp_path / "backups", f"assistant-2026-11-0{day}.db", keep=2)
    names = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert names == ["assistant-2026-11-03.db", "assistant-2026-11-04.db"]
    with sqlite3.connect(copy) as c:
        assert c.execute("SELECT x FROM t").fetchone() == (42,)
