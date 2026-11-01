"""Feature modules. Register each collector here (importing it registers its tables)."""

import assistant.modules.notes.models  # noqa: F401  no collector, but its table must exist
from assistant.core.collector import Collector
from assistant.modules.alarm.collector import AlarmPlanner, AlarmRinger
from assistant.modules.biedronka.collector import BiedronkaCollector
from assistant.modules.birthdays.collector import BirthdayReminder
from assistant.modules.books.collector import BooksCollector
from assistant.modules.calendar.collector import CalendarCollector
from assistant.modules.events.collector import EventsCollector
from assistant.modules.films.collector import FilmsCollector
from assistant.modules.flights.collector import FlightsCollector
from assistant.modules.jobs.collector import JobsCollector
from assistant.modules.mpk.alerts import LeaveNowAlert
from assistant.modules.mpk.collector import GeocodeCollector, MpkCollector
from assistant.modules.news.collector import NewsCollector, NewsDigestCollector
from assistant.modules.sports.collector import SportsCollector
from assistant.modules.weather.collector import WeatherCollector

COLLECTORS: list[Collector] = [
    WeatherCollector(),
    CalendarCollector(),
    MpkCollector(),
    GeocodeCollector(),
    LeaveNowAlert(),
    AlarmPlanner(),
    AlarmRinger(),
    BirthdayReminder(),
    JobsCollector(),
    EventsCollector(),
    FlightsCollector(),
    NewsCollector(),
    NewsDigestCollector(),
    FilmsCollector(),
    BooksCollector(),
    SportsCollector(),
    BiedronkaCollector(),
]
