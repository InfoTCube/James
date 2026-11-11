"""InPost's public tracking API (ShipX, no key)."""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import httpx

API = "https://api-shipx-pl.easypack24.net/v1"
HEADERS = {"User-Agent": "assistant/0.1 (personal home dashboard)"}


@dataclass
class Tracking:
    status: str
    status_at: datetime | None
    locker: str | None


def parse(data: dict) -> Tracking:
    """A tracking response → status, when it was set, and the locker (for locker parcels)."""
    details = data.get("tracking_details") or []
    at = datetime.fromisoformat(details[0]["datetime"]) if details else None
    m = (data.get("custom_attributes") or {}).get("target_machine_detail") or {}
    locker = None
    if m.get("name"):
        address = (m.get("address") or {}).get("line1")
        locker = m["name"] + (f", {address}" if address else "")
        if m.get("location_description"):
            locker += f" ({m['location_description']})"
    return Tracking(data["status"], at, locker)


def track(number: str) -> Tracking | None:
    """The parcel's tracking, None if InPost doesn't know it (yet). Raises on other errors."""
    resp = httpx.get(f"{API}/tracking/{number}", headers=HEADERS, timeout=20)
    if resp.status_code == 404:
        return None
    return parse(resp.raise_for_status().json())


def parse_titles(data: dict) -> dict[str, str]:
    return {i["name"]: i["title"].rstrip(".") for i in data["items"]}


@lru_cache(maxsize=1)
def status_titles() -> dict[str, str]:
    """Status name → English title, e.g. "ready_to_pickup" → "Ready for collection"."""
    resp = httpx.get(f"{API}/statuses", params={"lang": "en_GB"}, headers=HEADERS, timeout=20)
    return parse_titles(resp.raise_for_status().json())
