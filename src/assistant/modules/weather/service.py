"""Weather reads + clothing rules. No network here."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.modules.weather.models import WeatherHourly

QUICK_TRIP = timedelta(hours=1)  # no more events today: dress for a short trip out now

# WMO code -> (label, icon). Codes: https://open-meteo.com/en/docs (bottom of page)
CODES = {
    0: ("Clear", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Fog", "🌫️"),
    51: ("Drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Drizzle", "🌦️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"),
    80: ("Showers", "🌦️"), 81: ("Showers", "🌧️"), 82: ("Heavy showers", "⛈️"),
    85: ("Snow showers", "🌨️"), 86: ("Snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm, hail", "⛈️"), 99: ("Thunderstorm, hail", "⛈️"),
}  # fmt: skip


def describe(code: int) -> tuple[str, str]:
    """(label, icon) for a WMO weather code."""
    return CODES.get(code, ("Unknown", "❔"))


def get_hours(session: Session, start: datetime, end: datetime) -> list[WeatherHourly]:
    """Forecast hours with start <= time <= end, ordered."""
    q = select(WeatherHourly).where(WeatherHourly.time.between(start, end))
    return list(session.scalars(q.order_by(WeatherHourly.time)))


Window = tuple[datetime, datetime]

TRAVEL = timedelta(minutes=30)  # outside before the first event and after the last
LONG_BREAK = timedelta(hours=1)  # breaks longer than this may be spent outside


def outdoor_windows(events: list[Window], now: datetime) -> list[Window]:
    """When you're likely outside on a day of events (you're inside during events):
    TRAVEL before the first, TRAVEL after the last, and every break longer than LONG_BREAK.
    Past parts are dropped."""
    merged: list[list[datetime]] = []
    for start, end in sorted(events):
        if merged and start <= merged[-1][1]:  # overlapping events → one block
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if not merged:
        return []
    windows = [(merged[0][0] - TRAVEL, merged[0][0])]
    windows += [(a[1], b[0]) for a, b in pairwise(merged) if b[0] - a[1] > LONG_BREAK]
    windows.append((merged[-1][1], merged[-1][1] + TRAVEL))
    return [(max(s, now), e) for s, e in windows if e > now]


def default_window(now: datetime) -> Window:
    """No more events today: the next QUICK_TRIP, e.g. 12:22-13:22."""
    return now, now + QUICK_TRIP


def hours_in(session: Session, windows: list[Window]) -> list[WeatherHourly]:
    """Forecast hours overlapping any [start, end) window (the hour a window starts in counts;
    an hour exactly at the end doesn't, since you're already inside then)."""
    seen: dict[datetime, WeatherHourly] = {}
    for start, end in windows:
        for h in get_hours(session, start.replace(minute=0, second=0, microsecond=0), end):
            if h.time < end:
                seen[h.time] = h
    return sorted(seen.values(), key=lambda h: h.time)


@dataclass
class Advice:
    layers: str
    extras: list[str]


def _layers(feels: float) -> str:
    if feels < 0:
        return "Winter jacket, hat and gloves"
    if feels < 8:
        return "Warm jacket"
    if feels < 14:
        return "Light jacket"
    if feels < 19:
        return "Sweater or hoodie"
    return "T-shirt weather"


def clothing_advice(hours: list[WeatherHourly], tz: ZoneInfo) -> Advice | None:
    """Rules over the whole time out: dress for the coldest part, warn about rain/wind/heat."""
    if not hours:
        return None
    coldest = min(hours, key=lambda h: h.feels_like)
    extras = []

    first = hours[0].feels_like
    if first - coldest.feels_like >= 5 and _layers(first) != _layers(coldest.feels_like):
        at = coldest.time.astimezone(tz).strftime("%H:%M")
        extras.append(f"Starts at {first:.0f}°, drops to {coldest.feels_like:.0f}° by {at}")

    rainy = [h for h in hours if h.precip_prob >= 50 or h.precip_mm >= 0.5]
    if rainy:
        at = rainy[0].time.astimezone(tz).strftime("%H:%M")
        extras.append(f"Take an umbrella, rain from {at}")

    if max(h.wind_kmh for h in hours) >= 40:
        extras.append("Very windy")
    if max(h.feels_like for h in hours) >= 27:
        extras.append("Hot, take water")

    return Advice(layers=_layers(coldest.feels_like), extras=extras)


def get_clothing_advice(
    session: Session, now: datetime, tz: ZoneInfo, events: list[Window] | None = None
) -> Advice | None:
    """Clothing advice for the time you're outside today. None if there's no forecast data.

    `events`: today's (start, end) events you go out for (the calendar module passes these).
    No more events today → a short trip out now (QUICK_TRIP).
    """
    windows = outdoor_windows(events, now) if events else []
    return clothing_advice(hours_in(session, windows or [default_window(now)]), tz)
