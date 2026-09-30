"""Telegram reminders: the evening before (time to get a gift) and the morning of."""

from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.birthdays import service


class BirthdayReminder:
    name = "birthdays"
    schedule = CronTrigger(hour="9,18", minute=0)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        today = now.astimezone(config.tz).date()
        # morning run → today's birthdays; evening run → tomorrow's
        target = today if now.astimezone(config.tz).hour < 12 else today + timedelta(days=1)
        for b in service.upcoming(session, now, config.tz, days=2):
            if b.day == target:
                word = service.when(b.day, today)
                key = f"bday:{b.name}:{b.day}:{word}"
                notify(session, f"🎂 {b.name}'s birthday {word}!", key=key)
