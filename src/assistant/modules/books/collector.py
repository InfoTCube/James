"""Well-rated books on your subjects (Open Library) → once a month the AI picks a few."""

import logging
import time
from datetime import date

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.config import BooksConfig, load_config
from assistant.core.db import utcnow
from assistant.modules.books import service
from assistant.modules.books.models import Book

log = logging.getLogger(__name__)

SEARCH = "https://openlibrary.org/search.json"
HEADERS = {"User-Agent": "James personal home assistant (a few requests a month)"}
PER_SUBJECT = 25
MAX_SEEN = 100  # newest read books shown to the AI: keeps the input small
FIELDS = "key,title,author_name,first_publish_year,ratings_average,ratings_count,language"


def parse_books(data: dict, cfg: BooksConfig, seen: set[str], month: date) -> list[Book]:
    """A search response → candidates: rated by enough people, in one of your languages, not in
    `seen`."""
    books = []
    for d in data["docs"]:
        work = d["key"].rsplit("/", 1)[-1]
        if work in seen or d.get("ratings_count", 0) < cfg.min_ratings:
            continue
        if cfg.languages and not set(cfg.languages) & set(d.get("language", [])):
            continue
        books.append(
            Book(
                id=work,
                month=month,
                title=d["title"],
                author=", ".join(d.get("author_name", ["unknown"])[:2]),
                year=d.get("first_publish_year"),
                rating=float(d["ratings_average"]),
                link=f"https://openlibrary.org{d['key']}",
                picked=False,
            )
        )
    return books


def fetch_candidates(cfg: BooksConfig, seen: set[str], month: date) -> list[Book]:
    """The best rated books of each subject since `since_year`. One subject's failure fails the
    run, which is retried the next hour."""
    books: dict[str, Book] = {}
    for i, subject in enumerate(cfg.subjects):
        if i:
            time.sleep(1)  # ponytail: a few requests a month, keep them slow
        query = f'subject:"{subject}" first_publish_year:[{cfg.since_year} TO {month.year}]'
        params = {"q": query, "sort": "rating", "limit": PER_SUBJECT, "fields": FIELDS}
        resp = httpx.get(SEARCH, params=params, headers=HEADERS, timeout=60, follow_redirects=True)
        for b in parse_books(resp.raise_for_status().json(), cfg, seen, month):
            books.setdefault(b.id, b)
    return list(books.values())


def prompt(taste: str) -> str:
    wants = taste or "not stated, so choose varied, widely loved books"
    return (
        f"Pick the {service.PICKS} books this person would most enjoy this month. Their taste: "
        f'{wants}. The input lists the candidates, one per line as "id | title | author | year | '
        'rating". It may first list books they have already read: use those to judge their '
        "taste, and never pick one of them, even under another title or translation. "
        f"Output only {service.PICKS} lines, best first, each as "
        '"id | one short sentence in English saying why this book suits them".'
    )


class BooksCollector:
    """Each night until this month has picks (a failed AI call is retried next hour or night;
    the card shows the best rated meanwhile). At worker start in the daytime it only fetches
    the candidates."""

    name = "books"
    schedule = CronTrigger(hour="1-5", minute=25)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        if not config.books.subjects:
            return
        month = service.month_of(now.astimezone(config.tz).date())
        known = service.candidates(session, month)
        if any(b.picked for b in known):
            return
        books = known or fetch_candidates(config.books, service.picked_ids(session), month)
        seen = service.read_books(session)
        keys = {r.key for r in seen}
        pool = [b for b in books if service.title_key(b.title) not in keys]
        answer = None
        if pool and ai.at_night(now):  # before writing: it takes a while
            lines = [f"{b.id} | {b.title} | {b.author} | {b.year} | {b.rating:.1f}" for b in pool]
            if seen:
                titles = [r.title for r in seen[:MAX_SEEN]]
                lines = ["Already read:", *titles, "", "Candidates:", *lines]
            answer = ai.ask("books", prompt(config.books.taste), "\n".join(lines))
            if answer is None:
                log.warning("books: AI selection failed; retried at the next run")
        if not known:
            session.execute(delete(Book).where(Book.picked.is_(False)))
            session.add_all(books)
        picks = ai.parse_picks(answer or "", {b.id for b in pool}, service.PICKS)
        for b in books:
            if b.id in picks:
                b.picked, b.why = True, picks[b.id]
