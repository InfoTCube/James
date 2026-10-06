"""Feeds → news_items (hourly), and overnight one AI-written digest of the last 24 h."""

import hashlib
import html
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.news import service
from assistant.modules.news.models import NewsDigest, NewsItem

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (personal home assistant; a few requests a day)"}
KEEP = timedelta(days=3)
ATOM = "{http://www.w3.org/2005/Atom}"


def when(text: str | None, fallback: datetime) -> datetime:
    """RSS (RFC 822) or Atom (ISO 8601) date → aware datetime; `fallback` if missing or odd."""
    if not text:
        return fallback
    try:
        d = parsedate_to_datetime(text) if text[:1].isalpha() or "," in text else None
        d = d or datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def item(feed: str, title: str | None, link: str | None, published: datetime) -> NewsItem | None:
    title, link = " ".join(html.unescape(title or "").split()), (link or "").strip()
    if not title or not link:
        return None
    sha = hashlib.sha1(link.encode()).hexdigest()
    return NewsItem(id=sha, feed=feed, title=title, link=link, published=published)


def parse_feed(data: bytes, feed: str, now: datetime) -> list[NewsItem]:
    """RSS 2.0 or Atom → items (those without a title or link are skipped)."""
    root = ET.fromstring(data)
    items = []
    if root.tag == f"{ATOM}feed":
        for e in root.iter(f"{ATOM}entry"):
            link = e.find(f"{ATOM}link[@rel='alternate']")
            if link is None:
                link = e.find(f"{ATOM}link")
            href = link.get("href") if link is not None else None
            date = e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")
            items.append(item(feed, e.findtext(f"{ATOM}title"), href, when(date, now)))
    else:
        for e in root.iter("item"):
            link = e.findtext("link") or e.findtext("guid")
            items.append(item(feed, e.findtext("title"), link, when(e.findtext("pubDate"), now)))
    return [i for i in items if i]


class NewsCollector:
    """Hourly: new headlines from every feed. A failing feed is skipped (warned once a day)."""

    name = "news"
    schedule = CronTrigger(minute=30)

    def run(self, session: Session) -> None:
        feeds, now = load_config().news.feeds, utcnow()
        if not feeds:
            return
        fetched, failed = [], []
        for name, url in feeds.items():
            try:
                resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                fetched += parse_feed(resp.content, name, now)
            except Exception as e:  # one broken feed mustn't hide the others
                log.exception("news: %s failed", name)
                failed.append((name, f"{type(e).__name__}: {e}"))
        if len(failed) == len(feeds):
            raise RuntimeError("all news feeds failed")
        for name, error in failed:
            notify(session, f"⚠️ News: {name} failed: {error}"[:500],
                   key=f"news-fail:{name}:{now.date()}")  # fmt: skip
        known = set(
            session.scalars(select(NewsItem.id).where(NewsItem.id.in_([i.id for i in fetched])))
        )
        session.execute(delete(NewsItem).where(NewsItem.published < now - KEEP))
        for i in {i.id: i for i in fetched}.values():  # a link twice in one feed: once
            if i.id not in known and i.published >= now - KEEP:
                session.add(i)


PROMPT = (
    "Write a short morning news digest in Polish for someone who lives in Wrocław, Poland. "
    'The input has headlines from the last 24 hours, one per line as "[source] headline" '
    "(many in Polish). Pick the most important stories, at most 8. Sources named after a town "
    "(e.g. Wrocław, Wieluń) are local news the reader cares about: always include the most "
    "important story from each such source that has any, and more of them if they matter to "
    "people there (transport, closures, local decisions, notable events), naming the town. "
    "Merge headlines about the same story. One plain, factual sentence each. Output only "
    'the bullet lines, each starting with "• ", most important first.'
)


class NewsDigestCollector:
    """Overnight (core.ai only calls Claude at night): one digest per day of the last 24 h's
    headlines. Runs hourly in the night window so a failed call is retried."""

    name = "news_digest"
    schedule = CronTrigger(hour="1-5", minute=45)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        today = now.astimezone(config.tz).date()
        if not config.news.feeds or not ai.at_night(now) or service.digest(session, today):
            return
        items = sorted(service.headlines(session, now - timedelta(days=1)), key=lambda i: i.feed)
        if not items:
            return
        text = ai.ask("news", PROMPT, "\n".join(f"[{i.feed}] {i.title}" for i in items))
        if text is None:  # the dashboard shows raw headlines meanwhile
            raise RuntimeError("AI digest failed; retried at the next run tonight")
        session.add(NewsDigest(day=today, text=text.strip(), made_at=now))
