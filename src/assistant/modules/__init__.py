"""Feature modules. Register each collector here (importing it registers its tables)."""

from assistant.core.collector import Collector
from assistant.modules.calendar.collector import CalendarCollector
from assistant.modules.mpk.collector import GeocodeCollector, MpkCollector
from assistant.modules.weather.collector import WeatherCollector

COLLECTORS: list[Collector] = [
    WeatherCollector(),
    CalendarCollector(),
    MpkCollector(),
    GeocodeCollector(),
]
