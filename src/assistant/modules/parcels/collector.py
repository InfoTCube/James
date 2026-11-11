"""Checks tracked InPost parcels; a Telegram message when one gets somewhere that matters."""

import logging
import time

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.core.notifier import notify
from assistant.modules.parcels import client, service

log = logging.getLogger(__name__)


class ParcelsCollector:
    name = "parcels"
    schedule = CronTrigger(hour="7-22", minute="*/20")  # lockers fill up in the day

    def run(self, session: Session) -> None:
        service.forget_done(session, utcnow())
        self.check(session)

    def check(self, session: Session, alert: bool = True) -> None:
        """Ask InPost about every parcel on the way; save changes (and alert on important ones,
        unless the caller answers the user itself, like /track). Raises on network errors."""
        tz = load_config().tz
        tracked = service.active(session)
        if not tracked:
            return
        try:
            titles = client.status_titles()
        except httpx.HTTPError:
            log.warning("parcels: no status titles, showing status names")
            titles = {}
        for p in tracked:
            t = client.track(p.number)
            time.sleep(0.5)  # ponytail: a handful of parcels, keep it polite
            if t is None or t.status == p.status:
                continue
            p.status, p.status_at, p.title = t.status, t.status_at, titles.get(t.status)
            p.locker = t.locker or p.locker
            if alert and t.status in service.ALERT:  # saved status: never alerted twice
                notify(session, service.format_parcel(p, tz), key=f"parcel:{p.number}:{t.status}")
