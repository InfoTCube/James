"""Matches of the teams and leagues in config (TheSportsDB)."""

import logging
import time
from datetime import UTC, date, datetime, timedelta

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.sports.models import SportsMatch, SportsTeam

log = logging.getLogger(__name__)

API = "https://www.thesportsdb.com/api/v1/json/123"  # 123: its public key, no signup, not a secret
HEADERS = {"User-Agent": "James personal home assistant (a few requests an hour)"}
SPORTS = {"Soccer", "Volleyball"}
PAUSE = 7  # seconds between requests: 3 s got 429 after ~12 requests, 6 s went through
RETRY_AFTER = 60  # seconds to wait after a 429 before trying once more
KEEP = timedelta(days=30)
LEAGUE_DAYS = range(-2, 8)  # days around today asked for, per league: scores and the coming week


def find_team(data: dict) -> tuple[str, str] | None:
    """A team search → (id, name) of the first football or volleyball team, if there is one."""
    for t in data.get("teams") or []:
        if t.get("strSport") in SPORTS:
            return t["idTeam"], t["strTeam"]
    return None


def parse_match(e: dict, source: str) -> SportsMatch | None:
    """An event → a match. Postponed ones and those without a start time are skipped."""
    if e.get("strPostponed") == "yes" or not e.get("strTimestamp"):
        return None
    started = e.get("intHomeScore") is not None and e.get("intAwayScore") is not None
    return SportsMatch(
        id=e["idEvent"],
        team=source,
        sport=e["strSport"],
        league=e["strLeague"],
        home=e["strHomeTeam"],
        away=e["strAwayTeam"],
        starts=datetime.fromisoformat(e["strTimestamp"]).replace(tzinfo=UTC),  # it's in UTC
        home_score=int(e["intHomeScore"]) if started else None,
        away_score=int(e["intAwayScore"]) if started else None,
    )


def get(path: str, **params) -> dict:
    """One request, slowly. A 429 is waited out and retried once, then it counts as a failure."""
    time.sleep(PAUSE)
    resp = httpx.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=30)
    if resp.status_code == 429:
        time.sleep(RETRY_AFTER)
        resp = httpx.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=30)
    return resp.raise_for_status().json()


def team_matches(team_id: str, name: str) -> list[SportsMatch]:
    """The next and last match. ponytail: the public key gives only one of each, so scores build
    up run by run; a paid key would give whole seasons."""
    events = get("eventsnext.php", id=team_id).get("events") or []
    events += get("eventslast.php", id=team_id).get("results") or []
    return [m for e in events if (m := parse_match(e, name))]


def league_matches(league_id: int, name: str, today: date) -> list[SportsMatch]:
    """Every match around today. ponytail: one request per day per league, since the public key
    caps the league-wide "next" and "past" endpoints at one match."""
    events = []
    for offset in LEAGUE_DAYS:
        day = today + timedelta(days=offset)
        events += get("eventsday.php", d=day.isoformat(), l=league_id).get("events") or []
    return [m for e in events if (m := parse_match(e, name))]


class SportsCollector:
    """Every 3 h. Teams are fetched first, as they matter most if the rate limit hits. A failing
    team or league is skipped with a Telegram warning; the run only fails if all do."""

    name = "sports"
    schedule = CronTrigger(hour="*/3", minute=40)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        teams, leagues = config.sports.teams, config.sports.leagues
        today = now.astimezone(config.tz).date()
        found, matches, unknown, failed = [], [], [], []
        for name in teams:
            try:
                known = session.get(SportsTeam, name)
                if known:
                    team_id = known.team_id
                else:
                    hit = find_team(get("searchteams.php", t=name))
                    if hit is None:
                        unknown.append(name)
                        continue
                    team_id = hit[0]
                    found.append(SportsTeam(name=name, team_id=team_id, title=hit[1]))
                matches += team_matches(team_id, name)
            except Exception as e:  # one source mustn't hide the others
                log.exception("sports: %s failed", name)
                failed.append((name, f"{type(e).__name__}: {e}"))
        for name, league_id in leagues.items():
            try:
                matches += league_matches(league_id, name, today)
            except Exception as e:
                log.exception("sports: %s failed", name)
                failed.append((name, f"{type(e).__name__}: {e}"))
        if failed and len(failed) == len(teams) + len(leagues):
            raise RuntimeError("all sports teams and leagues failed")
        for name in unknown:
            notify(session, f"⚠️ Sports: no team called {name}. Check sports.teams in config.yaml",
                   key=f"sports-unknown:{name}:{now.date()}")  # fmt: skip
        for name, error in failed:  # once a day per source, not every run
            notify(session, f"⚠️ Sports: {name} failed: {error}"[:500],
                   key=f"sports-fail:{name}:{now.date()}")  # fmt: skip
        session.add_all(found)
        for m in matches:
            session.merge(m)
        session.execute(delete(SportsMatch).where(SportsMatch.starts < now - KEEP))
        session.execute(delete(SportsMatch).where(SportsMatch.team.not_in([*teams, *leagues])))
