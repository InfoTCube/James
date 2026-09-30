"""Alarm actions with side effects (Telegram). Used by the worker (ringing, timeouts), the
dashboard's Stop/Snooze buttons, the bot's Telegram buttons, and later voice."""

from datetime import datetime

from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.notifier import notify
from assistant.modules.alarm import service
from assistant.modules.alarm.models import Alarm


def ring(session: Session, alarm: Alarm, now: datetime, config: Config) -> None:
    """Start ringing: the dashboard shows the bell and beeps; Telegram gets Stop/Snooze."""
    alarm.rung_at = now
    text = f"⏰ {alarm.at.astimezone(config.tz):%H:%M}" + (
        f" · {alarm.label}" if alarm.label else ""
    )
    buttons = [("Stop", f"alarm:stop:{alarm.id}"), ("Snooze 10 min", f"alarm:snooze:{alarm.id}")]
    notify(session, text, key=f"alarm:{alarm.id}:{alarm.snoozes}", buttons=buttons)


def stop(session: Session, alarm: Alarm, now: datetime, config: Config) -> bool:
    """Stop it and start the briefing (dashboard + Telegram). False if it wasn't ringing."""
    if not service.stop(alarm, now):
        return False
    notify(session, service.briefing(session, now, config), key=f"briefing:{alarm.id}")
    return True


def snooze(session: Session, alarm: Alarm, now: datetime, config: Config) -> bool:
    """Ring again in 10 minutes. False if it wasn't ringing."""
    if not service.snooze(alarm, now):
        return False
    notify(session, f"😴 Snoozed until {alarm.at.astimezone(config.tz):%H:%M}")
    return True
