# CLAUDE.md

Guidance for Claude Code when working in this repository. Read this fully before making changes, and keep it up to date when the architecture or conventions change.

## What this project is

A personal home assistant for one user, living in **Wrocław, Poland** (timezone `Europe/Warsaw`). It runs 24/7 on a small x86 mini PC (target: used Lenovo ThinkCentre Tiny class, Intel i3/i5 6th–7th gen, 8 GB RAM, no GPU) connected to a screen and a USB speakerphone. Development happens on a laptop first; the hardware is bought once the software works.

The user interacts with it through:
- a **dashboard** (web page shown full-screen in Chromium kiosk mode),
- a **Telegram bot** (alerts and quick commands when away from home),
- **voice** (later phase: wake word → speech-to-text → intent → text-to-speech).

UI and voice language: **English**. Data sources are often Polish (stop names, shop products, job boards) — handle Polish characters and diacritics correctly everywhere.

## Core principle: plain code first, AI last

Most features are scheduled data fetching + filtering rules. **Default to deterministic code.** Use AI only where listed in "AI usage" below, and every AI-backed feature must degrade gracefully (show raw data) if the AI call fails or quota is exhausted.

## Features

| Module | What it does | Data source | AI? |
|---|---|---|---|
| `weather` | Forecast + clothing advice covering the whole day out (e.g. take a jacket if returning late and it gets cold) | Open-Meteo (free, no key) + calendar end times | No — rules |
| `calendar` | Events + notifications; add events (bot/voice) | Google Calendar API (OAuth, `calendar.events` scope) | No |
| `mpk` | Tram/bus departures to the next calendar event's location | Wrocław open data GTFS (open-data.cui.wroclaw.pl) + live vehicle positions (mpk.wroc.pl, delay estimates) | No |
| `email` | Summary of important emails | Gmail API; rules filter first | Summary only |
| `birthdays` | Upcoming birthdays | Google Contacts / manual list in config | No |
| `jobs` | Interesting job offers | justjoin.it, nofluffjobs, pracuj.pl | Optional ranking |
| `films` | Weekly film suggestions | TMDB API (incl. PL streaming availability) | Selection |
| `books` | Monthly book suggestions | Open Library / Google Books | Selection |
| `sports` | Football/volleyball matches for chosen teams/leagues + scores | api-sports.io (free tiers) | No |
| `news` | News digest | RSS feeds from config | Summary |
| `biedronka` | Promotions on products the user actually buys | Biedronka website/leaflet (scraping) | Maybe for leaflet parsing |
| `notes` | Store and search notes | Local (SQLite and/or Markdown files) | No |
| `flights` | Good flight deals from WRO for configured date windows, checked ~2×/day | Ryanair fare endpoints (unofficial), others later | No |
| `alarm` | Alarms, tied to a morning briefing | Scheduler + audio | No |
| `music` | Play/find music | Spotify Connect (spotifyd/raspotify) + Spotify Web API | No |
| `events` | Hackathons & IT events | Crossweb.pl, Meetup, Devpost, MLH | No — keyword filters |

Cross-cutting: **morning briefing** (spoken/displayed summary after alarm), **Telegram alerts** (flights, jobs, events, "leave now for your tram"), **settings** in config files.

## Architecture

Monorepo. **Each feature is a module folder; containers are grouped by runtime role, not one per feature.**

```
.
├── CLAUDE.md
├── docker-compose.yml
├── pyproject.toml          # single uv workspace / project
├── .env.example            # secrets template (never commit .env)
├── config/
│   └── config.yaml         # user preferences: stop aliases, teams, keywords, flight windows, feeds...
├── src/assistant/
│   ├── core/               # shared: config loading, db, logging, time utils, ai wrapper, notifier
│   ├── modules/
│   │   ├── weather/
│   │   ├── calendar/
│   │   ├── mpk/
│   │   └── ...             # one folder per feature (see table)
│   └── services/
│       ├── api/            # FastAPI: JSON endpoints + dashboard (Jinja2 + htmx)
│       ├── worker/         # APScheduler: runs every module's collector on its schedule
│       ├── bot/            # Telegram bot
│       └── voice/          # later: openWakeWord → whisper → intents → Piper
├── data/                   # runtime data: SQLite db, caches, GTFS files (gitignored, Docker volume)
└── tests/
    └── fixtures/           # recorded API/HTML responses — tests never hit the network
```

Containers (docker-compose services): `api`, `worker`, `bot`, later `voice`. All share the `data/` volume and one SQLite database (WAL mode).

### Module contract

Each module folder contains:
- `collector.py` — fetches external data and stores it. Exposes a collector with a `name`, a `schedule` (cron-style or interval) and a `run()` method. Must be **idempotent** and safe to re-run.
- `models.py` — the module's own tables (prefixed with module name, e.g. `weather_hourly`).
- `service.py` — read logic used by the API, bot and voice (e.g. `get_clothing_advice(date)`). No I/O to external services here — read from the DB.
- `routes.py` — optional FastAPI router for the module's endpoints/dashboard widget.
- `client.py` — optional, for external calls that can't wait for a collector: authenticated APIs incl. writes (e.g. `calendar.client.add_event`) and live data (e.g. `mpk.client.vehicle_positions`, cached, `[]` on failure). Other modules may call its public functions.
- `README.md` — short: source, schedule, config keys, known fragility.

Rules:
- Modules don't import each other's internals. Cross-module needs go through `service.py` functions (e.g. `mpk` calls `calendar.service.next_event_with_location()`).
- Every collector run is recorded in a shared `collector_runs` table (module, started_at, finished_at, status, error). The dashboard shows data as **stale** instead of failing when a collector breaks.
- A failing collector must never crash the worker.

## Tech stack

- Python 3.12, **uv** for dependencies, **ruff** for lint/format, **pytest** for tests
- FastAPI + Jinja2 + htmx for the dashboard (no heavy frontend framework)
- APScheduler for scheduling
- SQLite via SQLAlchemy 2.x
- httpx for HTTP; selectolax or BeautifulSoup for scraping
- python-telegram-bot for the bot
- Voice (later): openWakeWord, whisper.cpp or faster-whisper (`base.en`), Piper TTS
- Docker Compose for running everything

Prefer well-known libraries; ask before adding a large or unusual dependency.

## AI usage

AI runs through the **Claude Code CLI in headless mode** using the user's Claude Pro subscription — there is no API key. All calls go through one wrapper in `core/ai.py`; never call `claude` from anywhere else.

The wrapper must:
- run `claude -p` with `--output-format json` and a low `--max-turns`,
- **pipe the input data in** (Claude must not fetch data itself),
- run from an **empty temporary working directory** so no project context is loaded (token usage),
- have a timeout, catch all errors and return `None` on failure,
- cache results so the same input isn't sent twice,
- log each call (module, duration, success) so usage can be monitored.

Auth: `CLAUDE_CODE_OAUTH_TOKEN` environment variable (generated once with `claude setup-token`). The `worker` container needs the Claude Code CLI installed.

Allowed AI uses: email summary, news summary, film/book selection, optional job ranking, optional leaflet parsing, voice fallback for requests the intent matcher doesn't understand. Anything else: ask first. The Pro quota is shared with the user's normal Claude usage — keep calls few and batched (daily/weekly jobs, not per-item loops).

## Conventions

- All datetimes timezone-aware; store UTC, display `Europe/Warsaw`.
- Configuration in `config/config.yaml`, validated with pydantic on startup; secrets only in `.env`.
- Place names: user refers to places by aliases (`home`, `uni`, `work`, `vb`) defined in `places` in config, mapped to GTFS stop **names** (stable across timetable versions, unlike IDs). Free text (calendar locations, later chat/voice) goes through `mpk.service.resolve()`: alias/keyword → stop name in text → fuzzy match. Compare text with `core.config.fold()` (case + Polish diacritics).
- Calendar events without a location get one from `location_rules` in config (e.g. work → office only on office days). User-editable; later the bot may edit them.
- Scrapers: identify with a sensible User-Agent, respect rate limits, cache responses, keep request frequency low. Isolate parsing so breakage is easy to fix, and cover it with fixture-based tests.
- Type hints everywhere; small functions; docstrings on public service functions.
- Tests: every module gets tests for its parsing and rules using fixtures in `tests/fixtures/<module>/`. No network in tests.
- Never commit secrets, tokens, `.env` or `data/`.

## Commands

```bash
uv sync                          # install dependencies
uv run pytest                    # run tests
uv run ruff check . && uv run ruff format .
uv run python -m assistant.services.worker --run-once weather   # run one collector manually
docker compose up --build        # run the whole system
uv run python -m assistant.services.api                          # dashboard on :8000
```

Adding a module: implement the `Collector` protocol from `core/collector.py` (`run(session)` runs in one
transaction, committed only on success) and append it to `COLLECTORS` in `modules/__init__.py`.
Use `is_stale()` to flag stale data in widgets. Datetime columns use `core.db.UTCDateTime`.

## Roadmap

Work in this order. Keep each step small and working end-to-end before moving on.

**Phase 1 — skeleton + daily essentials** ← current
- [x] Project skeleton: uv project, `core/` (config, db, logging, collector_runs), worker, api with empty dashboard, docker-compose, `.env.example`, `config/config.yaml`
- [x] `weather` module: Open-Meteo collector + clothing rules + dashboard widget (first module — proves the whole pattern, no API keys needed)
- [x] `calendar` module: Google Calendar API (read + `add_event`), widget, feeds weather advice
- [x] `mpk` module: GTFS import, departures for aliases, link with next calendar event + live delay estimates (transfers later)

**Phase 2 — alerts**
- [ ] Telegram bot + `core/notifier`
- [ ] `flights`, `jobs`, `events`

**Phase 3 — voice & alarm**
- [ ] `alarm` + morning briefing
- [ ] `voice` service with intent matcher; AI fallback
- [ ] `notes`

**Phase 4 — AI-assisted & the rest**
- [ ] `core/ai.py` wrapper
- [ ] `email`, `news`, `films`, `books`
- [ ] `sports`, `birthdays`, `biedronka`, `music`

## How to work in this repo

- Before starting a task, check the roadmap and the relevant module README.
- Propose a short plan for anything touching more than one module or `core/`.
- After finishing a roadmap item, tick it here and update the module README.
- If a design decision changes something in this file, update this file in the same change.
