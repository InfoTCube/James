"""Nightly copy of the SQLite database to data/backups, keeping the last KEEP."""

import sqlite3
from contextlib import closing
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import DB_PATH, utcnow

KEEP = 7


def backup(db: Path, folder: Path, name: str, keep: int = KEEP) -> Path:
    """Consistent copy of `db` (SQLite's backup API: safe while others write) as folder/name;
    then only the newest `keep` copies are kept. The path of the copy."""
    folder.mkdir(parents=True, exist_ok=True)
    dest, tmp = folder / name, folder / f"{name}.tmp"
    with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(tmp)) as out:
        src.backup(out)
    tmp.replace(dest)  # a half-written copy never looks like a backup
    for old in sorted(folder.glob("assistant-*.db"))[:-keep]:
        old.unlink()
    return dest


class BackupCollector:
    """ponytail: same disk as the database, so it covers mistakes and corruption, not a dead
    SSD; copy data/backups to a USB stick or the cloud if that matters."""

    name = "backup"
    schedule = CronTrigger(hour=3, minute=50)

    def run(self, session: Session) -> None:
        day = utcnow().astimezone(load_config().tz).date()
        backup(DB_PATH, DB_PATH.parent / "backups", f"assistant-{day}.db")
