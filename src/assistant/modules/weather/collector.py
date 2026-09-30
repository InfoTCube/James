"""Open-Meteo hourly forecast → weather_hourly."""

from datetime import UTC, datetime

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.modules.weather.models import WeatherHourly

URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = {  # Open-Meteo name -> column
    "temperature_2m": "temp",
    "apparent_temperature": "feels_like",
    "precipitation_probability": "precip_prob",
    "precipitation": "precip_mm",
    "wind_speed_10m": "wind_kmh",
    "weather_code": "code",
}
USER_AGENT = "assistant/0.1 (personal home dashboard)"


def parse(data: dict) -> list[WeatherHourly]:
    """Open-Meteo JSON (timeformat=unixtime) → rows. Hours with missing values are skipped."""
    h = data["hourly"]
    rows = []
    for i, ts in enumerate(h["time"]):
        values = {col: h[api][i] for api, col in FIELDS.items()}
        if None in values.values():
            continue
        rows.append(WeatherHourly(time=datetime.fromtimestamp(ts, UTC), **values))
    return rows


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
        resp = httpx.get(URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        for row in parse(resp.json()):
            session.merge(row)  # upsert by time: re-runs overwrite with the newer forecast
