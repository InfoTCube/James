"""Feature modules. Register each collector here (importing it registers its tables)."""

from assistant.core.collector import Collector
from assistant.modules.weather.collector import WeatherCollector

COLLECTORS: list[Collector] = [WeatherCollector()]
