"""Worker jobs: keep the automatic alarms in line with the calendar, and ring due alarms.

Ringing = the dashboard's bell screen + sound (it polls /api/alarm/state) and a Telegram
message with Stop/Snooze buttons. The ringer runs on the minute, so a timeout fires up to a
minute after RING_FOR; the dashboard stops beeping at RING_FOR itself.
"""

from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.modules.alarm import actions, service


class AlarmPlanner:
    """Plans today's and tomorrow's automatic alarm from the calendar."""

    name = "alarm_planner"
    schedule = IntervalTrigger(minutes=5)

    def run(self, session: Session) -> None:
        config = load_config()
        today = utcnow().astimezone(config.tz).date()
        for day in (today, today + timedelta(days=1)):
            service.plan_auto_alarm(session, day, config)


class AlarmRinger:
    """On the minute: start due alarms ringing; after RING_FOR without an answer, stop them
    (which starts the briefing)."""

    name = "alarm_ringer"
    schedule = CronTrigger(second=0)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        for alarm in service.due_alarms(session, now):
            actions.ring(session, alarm, now, config)
        for alarm in service.timed_out_alarms(session, now):
            actions.stop(session, alarm, now, config)
