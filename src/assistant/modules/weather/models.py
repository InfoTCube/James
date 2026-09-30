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
