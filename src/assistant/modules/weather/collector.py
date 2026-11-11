"""Open-Meteo hourly forecast → weather_hourly, air quality → weather_air (+ smog alert)."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.weather import service
from assistant.modules.weather.models import WeatherAir, WeatherHourly

log = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_FIELDS = {"pm2_5": "pm2_5", "pm10": "pm10", "european_aqi": "aqi"}
FIELDS = {  # Open-Meteo name -> column
    "temperature_2m": "temp",
    "apparent_temperature": "feels_like",
    "precipitation_probability": "precip_prob",
    "precipitation": "precip_mm",
    "wind_speed_10m": "wind_kmh",
    "weather_code": "code",
}
USER_AGENT = "assistant/0.1 (personal home dashboard)"


def parse(data: dict, fields: dict = FIELDS, model: type = WeatherHourly) -> list:
    """Open-Meteo JSON (timeformat=unixtime) → rows. Hours with missing values are skipped."""
    h = data["hourly"]
    rows = []
    for i, ts in enumerate(h["time"]):
        values = {col: h[api][i] for api, col in fields.items()}
        if None in values.values():
            continue
        rows.append(model(time=datetime.fromtimestamp(ts, UTC), **values))
    return rows


def parse_air(data: dict) -> list[WeatherAir]:
    return parse(data, AIR_FIELDS, WeatherAir)


def fetch(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    return resp.raise_for_status().json()


def alert_smog(session: Session, now: datetime) -> None:
    """One Telegram message a day when the next 12 h get very poor (AQI 80+)."""
    worst = service.worst_air(session, now, timedelta(hours=12))
    if worst and worst.aqi >= service.ALERT_AQI:
        day = worst.time.astimezone(load_config().tz).date()
        notify(session, service.air_advice(worst, now, load_config().tz), key=f"smog:{day}")


class WeatherCollector:
    name = "weather"
    schedule = IntervalTrigger(hours=1)

    def run(self, session: Session) -> None:
        loc = load_config().location
        params = {
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "hourly": ",".join(FIELDS),
            "forecast_days": 2,
            "timezone": "GMT",
            "timeformat": "unixtime",
        }
        for row in parse(fetch(URL, params)):
            session.merge(row)  # upsert by time: re-runs overwrite with the newer forecast
        try:  # a separate API: if it's down, the weather still updates
            air = parse_air(fetch(AIR_URL, {**params, "hourly": ",".join(AIR_FIELDS)}))
        except httpx.HTTPError:
            log.exception("weather: air quality failed")
            return
        for row in air:
            session.merge(row)
        session.flush()
        alert_smog(session, utcnow())
