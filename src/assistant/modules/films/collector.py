"""Well-rated films streaming in Poland (JustWatch) → once a week the AI picks a few."""

import logging
from datetime import date

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.config import FilmsConfig, fold, load_config
from assistant.core.db import utcnow
from assistant.modules.films import service
from assistant.modules.films.models import Film

log = logging.getLogger(__name__)

API = "https://apis.justwatch.com/graphql"  # unofficial: what justwatch.com itself uses, no key
SITE = "https://www.justwatch.com"
HEADERS = {"User-Agent": "James personal home assistant (a few requests a week)"}
COUNT = 40
MAX_SEEN = 100  # newest watched films shown to the AI: keeps the input small
PACKAGES = """
query Packages($country: Country!) {
  packages(country: $country, platform: WEB) { clearName shortName }
}"""
POPULAR = """
query Popular($country: Country!, $language: Language!, $first: Int!, $filter: TitleFilter) {
  popularTitles(country: $country, first: $first, filter: $filter, sortBy: POPULAR) {
    edges { node { objectId
      content(country: $country, language: $language) {
        title originalReleaseYear shortDescription fullPath
        scoring { imdbScore }
      }
      offers(country: $country, platform: WEB, filter: {monetizationTypes: [FLATRATE]}) {
        package { clearName }
      }
    } }
  }
}"""


def graphql(query: str, **variables) -> dict:
    """One query's `data`. A GraphQL error (HTTP 200 with `errors`) counts as a failure."""
    resp = httpx.post(
        API, json={"query": query, "variables": variables}, headers=HEADERS, timeout=30
    )
    body = resp.raise_for_status().json()
    if body.get("errors"):
        raise RuntimeError(f"JustWatch: {str(body['errors'])[:300]}")
    return body["data"]


def package_codes(packages: list[dict], wanted: list[str]) -> list[str]:
    """Service names from config → JustWatch codes. An unknown name is skipped with a warning."""
    codes = {fold(p["clearName"]): p["shortName"] for p in packages}
    for name in wanted:
        if fold(name) not in codes:
            log.warning("films: unknown streaming service %r in config", name)
    found = [codes[fold(name)] for name in wanted if fold(name) in codes]
    if not found:
        raise ValueError(f"none of the streaming services are known to JustWatch: {wanted}")
    return found


def parse_films(data: dict, cfg: FilmsConfig, seen: set[int], week: date) -> list[Film]:
    """A popularTitles response → candidates, minus `seen`. With `providers` in config only
    those services are named; a film with no service left is dropped."""
    wanted = {fold(p) for p in cfg.providers}
    films = []
    for edge in data["popularTitles"]["edges"]:
        node, content = edge["node"], edge["node"]["content"]
        names = dict.fromkeys(o["package"]["clearName"] for o in node["offers"])  # no duplicates
        names = [n for n in names if not wanted or fold(n) in wanted]
        if node["objectId"] in seen or not names:
            continue
        films.append(
            Film(
                id=node["objectId"],
                week=week,
                title=content["title"],
                year=content.get("originalReleaseYear"),
                overview=content.get("shortDescription") or "",
                rating=float(content["scoring"]["imdbScore"] or 0),
                providers=", ".join(names[:3]),
                link=SITE + content["fullPath"],
                picked=False,
            )
        )
    return films


def fetch_candidates(cfg: FilmsConfig, seen: set[int], week: date) -> list[Film]:
    """Popular well-rated films on subscription streaming in Poland, minus the ones in `seen`."""
    title_filter = {
        "objectTypes": ["MOVIE"],
        "monetizationTypes": ["FLATRATE"],
        "imdbScore": {"min": cfg.min_rating},
        "imdbVotes": {"min": cfg.min_votes},
    }
    if cfg.providers:
        packages = graphql(PACKAGES, country="PL")["packages"]
        title_filter["packages"] = package_codes(packages, cfg.providers)
    data = graphql(POPULAR, country="PL", language="en", first=COUNT, filter=title_filter)
    return parse_films(data, cfg, seen, week)


def prompt(taste: str) -> str:
    wants = taste or "not stated, so choose varied, widely loved films"
    return (
        f"Pick the {service.PICKS} films this person would most enjoy this week. Their taste: "
        f'{wants}. The input lists the candidates, one per line as "id | title (year) | rating | '
        'overview". It may first list films they have already watched: use those to judge their '
        "taste, and never pick one of them, even under another title or translation. "
        f"Output only {service.PICKS} lines, best first, each as "
        '"id | one short sentence in English saying why this film suits them".'
    )


class FilmsCollector:
    """Each night until this week has picks (a failed AI call is retried next hour or night;
    the card shows the best rated meanwhile). At worker start in the daytime it only fetches
    the candidates."""

    name = "films"
    schedule = CronTrigger(hour="1-5", minute=15)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        week = service.week_of(now.astimezone(config.tz).date())
        known = service.candidates(session, week)
        if any(f.picked for f in known):
            return
        films = known or fetch_candidates(config.films, service.picked_ids(session), week)
        seen = service.watched(session)
        keys = {w.key for w in seen}
        pool = [f for f in films if service.title_key(f.title) not in keys]
        answer = None
        if pool and ai.at_night(now):  # before writing: it takes a while
            lines = [f"{f.id} | {f.title} ({f.year}) | {f.rating:.1f} | {f.overview[:200]}"
                     for f in pool]  # fmt: skip
            if seen:
                titles = [w.title for w in seen[:MAX_SEEN]]
                lines = ["Already watched:", *titles, "", "Candidates:", *lines]
            answer = ai.ask("films", prompt(config.films.taste), "\n".join(lines))
            if answer is None:
                log.warning("films: AI selection failed; retried at the next run")
        if not known:
            session.execute(delete(Film).where(Film.picked.is_(False)))
            session.add_all(films)
        picks = ai.parse_picks(answer or "", {str(f.id) for f in pool}, service.PICKS)
        for f in films:
            if str(f.id) in picks:
                f.picked, f.why = True, picks[str(f.id)]
