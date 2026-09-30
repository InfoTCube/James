"""Alarm on the dashboard: state for the bell screen, Stop/Snooze buttons, the briefing."""

from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.alarm import actions, service
from assistant.modules.alarm.models import Alarm

router = APIRouter()


@router.get("/api/alarm/state")
def alarm_state() -> dict:
    """What the dashboard should show: "ringing" (bell + sound), "briefing", or "idle"."""
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        if alarm := service.ringing_alarm(session, now):
            return {
                "state": "ringing",
                "id": alarm.id,
                "time": f"{alarm.at.astimezone(config.tz):%H:%M}",
                "label": alarm.label or "",
                "seconds_left": int((alarm.rung_at + service.RING_FOR - now).total_seconds()),
            }
        if alarm := service.briefing_alarm(session, now):
            return {"state": "briefing", "id": alarm.id}
    return {"state": "idle"}


def _act(alarm_id: int, action) -> dict:
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session, session.begin():
        alarm = session.get(Alarm, alarm_id)
        if alarm is None or not action(session, alarm, now, config):
            raise HTTPException(409, "That alarm isn't ringing")
    return alarm_state()


# ponytail: no auth, like the rest of the dashboard; fine on a home network, not on the internet.
@router.post("/api/alarm/{alarm_id}/stop")
def stop_alarm(alarm_id: int) -> dict:
    return _act(alarm_id, actions.stop)


@router.post("/api/alarm/{alarm_id}/snooze")
def snooze_alarm(alarm_id: int) -> dict:
    return _act(alarm_id, actions.snooze)


@router.get("/widgets/briefing")
def briefing_widget():
    """The morning briefing as HTML, one paragraph per line."""
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        text = service.briefing(session, now, config)
    return HTMLResponse("".join(f"<p>{escape(line)}</p>" for line in text.splitlines()))


@router.get("/widgets/alarm/next")
def next_alarm_widget():
    """The next alarm for the dashboard's corner, e.g. "⏰ 07:00 tomorrow"; empty if none."""
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session:
        upcoming = [a for a in service.upcoming_alarms(session, now) if a.at > now]
    if not upcoming:
        return HTMLResponse("")
    at = upcoming[0].at.astimezone(config.tz)
    days = (at.date() - now.astimezone(config.tz).date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else f"{at:%a}"
    return HTMLResponse(f"⏰ {at:%H:%M} <span>{day}</span>")
