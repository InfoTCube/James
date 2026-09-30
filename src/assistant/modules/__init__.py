"""Feature modules. Register each collector here (importing it registers its tables)."""

from assistant.core.collector import Collector

COLLECTORS: list[Collector] = []
