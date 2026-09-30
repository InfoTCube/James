"""Feature modules. Register each collector here (importing it registers its tables)."""

import assistant.modules.notes.models  # noqa: F401  no collector, but its table must exist
from assistant.core.collector import Collector
from assistant.modules.alarm.collector import AlarmPlanner, AlarmRinger
from assistant.modules.birthdays.collector import BirthdayReminder
from assistant.modules.calendar.collector import CalendarCollector
from assistant.modules.mpk.alerts import LeaveNowAlert
from assistant.modules.mpk.collector import GeocodeCollector, MpkCollector
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
]
