"""Job boards → jobs_offers (matches only) + one Telegram alert with the new ones."""

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.core.config import JobsConfig, fold, load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.jobs import service
from assistant.modules.jobs.models import JobOffer

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (personal home assistant; a few requests a day)"}
KEEP = timedelta(days=120)  # longer than offers stay listed, so old ones don't alert again
TELEGRAM_MAX = 4096  # characters per message


def iso(s: str) -> datetime:
    """ISO time in UTC with any fraction length ("…04.6700736Z") → aware datetime."""
    return datetime.fromisoformat(s[:19]).replace(tzinfo=UTC)


def money(lo: float | None, hi: float | None, currency: str, per: str) -> str | None:
    if not lo and not hi:
        return None
    amount = "–".join(f"{x:,.0f}".replace(",", " ") for x in (lo, hi) if x)
    return f"{amount} {currency}/{per.lower()}"


# workingTime → full-time only? B2B/freelance don't say, so unknown.
JUSTJOIN_FULL_TIME = {"full_time": True, "part_time": False, "practice_internship": False}


def unique(items) -> str:
    return ", ".join(dict.fromkeys(i for i in items if i))


# --- parsers: board response → JobOffer rows (first_seen is set by the collector) ---


def parse_justjoin(data: dict) -> list[JobOffer]:
    offers = []
    for o in data["data"]:
        pay = next((e for e in o["employmentTypes"] if e["currencySource"] == "original"), None)
        offers.append(
            JobOffer(
                id=f"justjoin:{o['slug']}",
                source="justjoin",
                title=o["title"],
                company=o["companyName"],
                url=f"https://justjoin.it/job-offer/{o['slug']}",
                # from/to are per month; the rate in its own unit (e.g. per hour) is *PerUnit
                salary=pay
                and money(pay["fromPerUnit"], pay["toPerUnit"], pay["currency"], pay["unit"]),
                seniority=o["experienceLevel"] or "",
                skills=unique(s["name"] for s in o["requiredSkills"] + o["niceToHaveSkills"]),
                cities=unique([o["city"], *(loc["city"] for loc in o["locations"])]),
                remote=o["workplaceType"] == "remote",
                hybrid=o["workplaceType"] == "hybrid",
                office_days=(o.get("hybridWorkSchedule") or {}).get("officeDays"),
                full_time=JUSTJOIN_FULL_TIME.get(o["workingTime"]),
                published=iso(o["publishedAt"]),
            )
        )
    return offers


def parse_nofluffjobs(data: dict) -> list[JobOffer]:
    offers = []
    for o in data["postings"]:
        places = [p.get("city") for p in o["location"]["places"]]
        pay = o.get("salary")
        offers.append(
            JobOffer(
                id=f"nofluffjobs:{o['id']}",
                source="nofluffjobs",
                title=o["title"],
                company=o["name"],
                url=f"https://nofluffjobs.com/pl/job/{o['url']}",
                salary=pay and money(pay["from"], pay["to"], pay["currency"], pay["period"]),
                seniority=", ".join(o.get("seniority", [])),
                skills=unique(
                    t["value"]
                    for t in o.get("tiles", {}).get("values", [])
                    if t["type"] == "requirement"
                ),
                cities=unique(c for c in places if c != "Remote"),
                remote=o.get("fullyRemote", False) or "Remote" in places,
                hybrid=bool(o["location"].get("hybridDesc", "").strip()),
                office_days=None,  # ponytail: only free text ("3 days a week", "6x/miesiąc")
                full_time=None,  # not in the listing
                published=datetime.fromtimestamp(o["posted"] / 1000, UTC),
            )
        )
    return offers


def parse_pracuj(html: str) -> list[JobOffer]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("pracuj.pl: no __NEXT_DATA__ in page (layout changed?)")
    data = json.loads(m[1])["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]
    offers = []
    for o in data["data"]["groupedOffers"]:
        offers.append(
            JobOffer(
                id=f"pracuj:{o['groupId']}",
                source="pracuj",
                title=o["jobTitle"],
                company=o["companyName"],
                url=o["offers"][0]["offerAbsoluteUri"],
                salary=o["salaryDisplayText"] or None,
                seniority=", ".join(o["positionLevels"]),
                skills=", ".join(o["technologies"]),
                cities=unique(p["displayWorkplace"].split(",")[0] for p in o["offers"]),
                remote="Praca zdalna" in o["workModes"],
                hybrid="Praca hybrydowa" in o["workModes"],
                office_days=None,  # not in the listing
                full_time=(o["workSchedules"] == ["Pełny etat"]) if o["workSchedules"] else None,
                published=iso(o["lastPublicated"]),
            )
        )
    return offers


# --- fetchers: one search per keyword, all of Poland (justjoin.it, pracuj.pl) or the configured
# cities + remote (nofluffjobs); where the offer is gets checked in service.matches ---


def fetch_justjoin(cfg: JobsConfig) -> list[JobOffer]:
    offers = []
    for kw in cfg.keywords:
        params = {
            "keywords": kw,
            "from": 0,
            "itemsCount": 100,
            "orderBy": "descending",
            "sortBy": "publishedAt",
        }
        url = "https://justjoin.it/api/candidate-api/offers"
        resp = httpx.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        offers += parse_justjoin(resp.json())
    return offers


def fetch_nofluffjobs(cfg: JobsConfig) -> list[JobOffer]:
    cities = [fold(c) for c in cfg.cities + cfg.hybrid_cities] + ["remote"] * cfg.remote
    offers = []
    for kw in cfg.keywords:
        resp = httpx.post(
            "https://nofluffjobs.com/api/search/posting",
            params={
                "pageTo": 1,
                "pageSize": 50,
                "salaryCurrency": "PLN",  # required
                "salaryPeriod": "month",  # required
                "region": "pl",
            },
            json={"rawSearch": f"{kw} city={','.join(cities)}"},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        offers += parse_nofluffjobs(resp.json())
    return offers


def fetch_pracuj(cfg: JobsConfig) -> list[JobOffer]:
    offers = []
    for kw in cfg.keywords:
        url = f"https://it.pracuj.pl/praca/{quote(kw)};kw"
        resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        offers += parse_pracuj(resp.text)
    return offers


SOURCES: dict[str, tuple[str, Callable[[JobsConfig], list[JobOffer]]]] = {
    "justjoin": ("justjoin.it", fetch_justjoin),
    "nofluffjobs": ("nofluffjobs", fetch_nofluffjobs),
    "pracuj": ("pracuj.pl", fetch_pracuj),
}


def save_new(session: Session, offers: list[JobOffer], cfg: JobsConfig) -> list[JobOffer]:
    """Store matching offers not seen before, on any board (by dedupe key); return them.
    Duplicates aren't stored: the first board's offer stands for the job."""
    now, new, seen = utcnow(), [], set()
    for o in offers:
        if not service.matches(o, cfg):
            continue
        o.dedupe_key = service.dedupe_key(o)
        if o.dedupe_key in seen or session.scalar(
            select(JobOffer.id).where(JobOffer.dedupe_key == o.dedupe_key).limit(1)
        ):
            continue
        seen.add(o.dedupe_key)
        o.first_seen = now
        session.add(o)
        new.append(o)
    return new


class JobsCollector:
    """All boards in one run, so a job listed on several boards is alerted once, in one message."""

    name = "jobs"
    schedule = CronTrigger(hour="8-20/4", minute=20)

    def run(self, session: Session) -> None:
        cfg = load_config().jobs
        if not cfg.keywords:
            return
        offers, failed = [], []
        for source, (label, fetch) in SOURCES.items():  # before any write: no DB lock during HTTP
            try:
                offers += fetch(cfg)
            except Exception as e:  # one broken board mustn't hide the others' offers
                log.exception("jobs: %s failed", label)
                failed.append((source, label, f"{type(e).__name__}: {e}"))
        if len(failed) == len(SOURCES):
            raise RuntimeError("all job boards failed")
        today = utcnow().date()
        for source, label, error in failed:  # once a day per board, not every run
            notify(
                session, f"⚠️ Jobs: {label} failed: {error}"[:500], key=f"jobs-fail:{source}:{today}"
            )
        session.execute(delete(JobOffer).where(JobOffer.first_seen < utcnow() - KEEP))
        new = save_new(session, offers, cfg)
        if new:
            notify(session, alert_text(new, cfg))


def alert_text(new: list[JobOffer], cfg: JobsConfig) -> str:
    """One message with as many offers as fit Telegram's 4096 characters, best fit first."""
    new = service.rank(new, cfg)
    parts = [f"💼 {len(new)} new job{'s' * (len(new) > 1)}"]
    for i, o in enumerate(new):
        more = f"…and {len(new) - i} more in /jobs"
        if len("\n\n".join([*parts, service.format_offer(o, cfg), more])) > TELEGRAM_MAX:
            parts.append(more)
            break
        parts.append(service.format_offer(o, cfg))
    return "\n\n".join(parts)
