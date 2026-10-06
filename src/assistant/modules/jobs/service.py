"""Which offers match the config, and reading stored matches. No network here."""

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import JobsConfig, fold, has_word
from assistant.modules.jobs.models import JobOffer

# Words that don't tell companies apart: legal forms ("Sp. z o.o.", "S.A."), "Poland", "Group".
COMPANY_NOISE = {"sp", "z", "o", "oo", "s", "a", "sa", "k", "j", "spolka", "akcyjna",
                 "ograniczona", "odpowiedzialnoscia", "komandytowa", "ltd", "inc", "llc",
                 "gmbh", "ag", "bv", "poland", "polska", "group", "grupa"}  # fmt: skip


def words(text: str) -> list[str]:
    return re.sub(r"[^\w]+", " ", fold(text)).split()


def dedupe_key(offer: JobOffer) -> str:
    """Same job on several boards (or once per city) → same key: company without legal form +
    title words in any order, without gender tags like "(k/m/n)". "C#" keeps its "c"."""
    company = " ".join(w for w in words(offer.company) if w not in COMPANY_NOISE)
    title = sorted({w for w in words(offer.title) if len(w) > 1 or w.isdigit() or w == "c"})
    return f"{company}|{' '.join(title)}"


def in_any(offer: JobOffer, cities: list[str]) -> bool:
    return any(fold(c) in fold(offer.cities) for c in cities)


def place_ok(offer: JobOffer, cfg: JobsConfig) -> bool:
    """In a home city; or remote; or hybrid in a hybrid city with few (or unknown) office days."""
    return (
        in_any(offer, cfg.cities)
        or (cfg.remote and offer.remote)
        or (
            offer.hybrid
            and in_any(offer, cfg.hybrid_cities)
            and (offer.office_days is None or offer.office_days <= cfg.max_office_days)
        )
    )


def matches(offer: JobOffer, cfg: JobsConfig) -> bool:
    """True if the offer is worth an alert under `cfg` (see the jobs section in config.yaml)."""
    about = f"{offer.title} {offer.skills}"
    return (
        any(has_word(about, k) for k in cfg.keywords)
        and not any(has_word(offer.title, x) for x in cfg.exclude)
        and (
            not cfg.seniority
            or not offer.seniority
            or any(fold(level) in fold(offer.seniority) for level in cfg.seniority)
        )
        and place_ok(offer, cfg)
    )


def recent(session: Session, now: datetime, cfg: JobsConfig, days: int = 7) -> list[JobOffer]:
    """Matching offers first seen in the last `days` days, best fit first (see `rank`)."""
    since = now - timedelta(days=days)
    return rank(session.scalars(select(JobOffer).where(JobOffer.first_seen >= since)), cfg)


def fit(offer: JobOffer, cfg: JobsConfig) -> int:
    """0–3 points, one each for: a keyword in the title (a .NET job, not one that mentions it),
    in a home city or remote (not just hybrid elsewhere), and a level the board states and that
    matches `seniority` (not merely unstated; free when `seniority` is empty)."""
    return (
        any(has_word(offer.title, k) for k in cfg.keywords)
        + (in_any(offer, cfg.cities) or (cfg.remote and offer.remote))
        + (
            not cfg.seniority
            or any(fold(level) in fold(offer.seniority) for level in cfg.seniority)
        )
    )


def best_fit(offer: JobOffer, cfg: JobsConfig) -> bool:
    """All of `fit`'s points: marked ★."""
    return fit(offer, cfg) == 3


def rank(offers, cfg: JobsConfig) -> list[JobOffer]:
    """Best fit first, then newest."""
    return sorted(offers, key=lambda o: (fit(o, cfg), o.published), reverse=True)


def work_mode(o: JobOffer) -> str:
    """How you'd work: remote, hybrid (with office days if known) or office."""
    if o.remote:
        return "remote"
    if o.hybrid:
        days = o.office_days
        return (
            f"hybrid, {days} office day{'s' * (days != 1)}/week"
            if days
            else "hybrid (office days?)"
        )
    return "office"


def hours(o: JobOffer) -> str | None:
    """Display text for working hours; None when the board doesn't say."""
    return {True: "full-time", False: "part-time OK"}.get(o.full_time)


def details(o: JobOffer, max_cities: int = 3) -> str:
    """Salary · cities · work mode · hours, whatever is known."""
    cities = o.cities.split(", ")
    more = len(cities) - max_cities
    where = ", ".join(cities[:max_cities]) + (f" +{more}" if more > 0 else "")
    return " · ".join(filter(None, [o.salary, where, work_mode(o), hours(o)]))


def format_offer(o: JobOffer, cfg: JobsConfig) -> str:
    """Three lines for Telegram: [★] title – company, details, link."""
    star = "⭐ " if best_fit(o, cfg) else ""
    return f"{star}{o.title} – {o.company}\n{details(o)}\n{o.url}"
