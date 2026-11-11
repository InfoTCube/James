from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class WeatherHourly(Base):
    __tablename__ = "weather_hourly"

    time: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    temp: Mapped[float]  # °C
    feels_like: Mapped[float]  # °C, apparent temperature
    precip_prob: Mapped[int]  # %
    precip_mm: Mapped[float]
    wind_kmh: Mapped[float]
    code: Mapped[int]  # WMO weather code


class WeatherAir(Base):
    """Hourly air quality forecast (Open-Meteo, CAMS Europe)."""

    __tablename__ = "weather_air"

    time: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    pm2_5: Mapped[float]  # µg/m³
    pm10: Mapped[float]  # µg/m³
    aqi: Mapped[int]  # European AQI: 0–20 good … 80–100 very poor, 100+ extremely poor
