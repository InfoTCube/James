"""Live vehicle positions from MPK Wrocław. The only live (per-request) network call in mpk."""

import logging
import time

import httpx

log = logging.getLogger(__name__)

POSITIONS_URL = "https://mpk.wroc.pl/bus_position"
HEADERS = {"User-Agent": "assistant/0.1 (personal home dashboard)"}
CACHE_SECONDS = 20

_cache: dict[tuple, tuple[float, list[dict]]] = {}


def vehicle_positions(trams: set[str], buses: set[str]) -> list[dict]:
    """Raw positions for these lines: [{"name", "type", "x": lat, "y": lon, "k"}].
    Cached briefly; returns [] on any failure (live data is a nice-to-have)."""
    key = (frozenset(trams), frozenset(buses))
    if (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    form = {"busList[tram][]": sorted(trams), "busList[bus][]": sorted(buses)}
    try:
        data = httpx.post(POSITIONS_URL, data=form, headers=HEADERS, timeout=5).json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("live positions failed: %s", e)
        return []
    _cache[key] = (time.monotonic(), data)
    return data
