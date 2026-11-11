# backup

- **What:** a copy of `data/assistant.db` every night at 03:50 to `data/backups/assistant-YYYY-MM-DD.db`,
  keeping the last 7 (about 90 MB each, mostly the GTFS timetable). It uses SQLite's backup API,
  so the copy is consistent while other services write. It also runs at worker start (same day:
  overwritten).
- **Restore:** stop everything (`docker compose down`), copy a backup over `data/assistant.db`,
  delete `assistant.db-wal` and `assistant.db-shm`, start again. In Docker the files are in the
  `data` volume: `docker compose run --rm worker sh` and copy them there.
- **Limit:** same disk as the database. It covers mistakes and corruption, not a dead SSD.
