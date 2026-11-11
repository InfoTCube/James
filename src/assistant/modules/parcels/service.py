"""Tracked parcels: add/remove, the list, and how to show one. No network here."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.modules.parcels.models import Parcel

NUMBER = re.compile(r"\d{24}")  # InPost tracking numbers are 24 digits
DONE = {"delivered", "returned_to_sender", "canceled"}  # no more checks
KEEP_DONE = timedelta(days=2)  # a finished parcel stays on the list this long
# worth a Telegram message; the rest (sorting centres…) only show on the list
ALERT = {
    "out_for_delivery", "out_for_delivery_to_address", "ready_to_pickup",
    "ready_to_pickup_from_pok", "ready_to_pickup_from_branch", "pickup_reminder_sent",
    "pickup_time_expired", "stack_in_box_machine", "stack_in_customer_service_point",
    "stack_parcel_pickup_time_expired", "stack_parcel_in_box_machine_pickup_time_expired",
    "avizo", "delay_in_delivery", "undelivered", "returned_to_sender", "missing",
    "rejected_by_receiver", "undelivered_wrong_address", "undelivered_incomplete_address",
    "undelivered_unknown_receiver", "redirect_to_box", "readdressed",
}  # fmt: skip


def find_numbers(text: str) -> list[str]:
    """InPost tracking numbers in free text (a /track message, later an email)."""
    return list(dict.fromkeys(NUMBER.findall(re.sub(r"(?<=\d)[ -](?=\d)", "", text))))


def add(session: Session, number: str, label: str, now: datetime) -> Parcel:
    """Track a parcel (again: just renames it)."""
    parcel = session.get(Parcel, number) or Parcel(number=number, added_at=now)
    parcel.label = label
    session.add(parcel)
    return parcel


def remove(session: Session, ref: str) -> Parcel | None:
    """Stop tracking: by number, its last digits or its label; the parcel, or None."""
    ref = ref.strip().lower()
    for p in parcels(session):
        if ref and (p.number.endswith(ref) or p.label.lower() == ref):
            session.delete(p)
            return p
    return None


def parcels(session: Session) -> list[Parcel]:
    """All tracked parcels, newest first."""
    return list(session.scalars(select(Parcel).order_by(Parcel.added_at.desc())))


def active(session: Session) -> list[Parcel]:
    """Parcels still on the way (to check with InPost)."""
    return [p for p in parcels(session) if p.status not in DONE]


def forget_done(session: Session, now: datetime) -> None:
    """Drop parcels finished more than KEEP_DONE ago."""
    session.execute(
        delete(Parcel).where(Parcel.status.in_(DONE), Parcel.status_at < now - KEEP_DONE)
    )


def name(p: Parcel) -> str:
    return p.label or f"…{p.number[-6:]}"


def format_parcel(p: Parcel, tz: ZoneInfo) -> str:
    """E.g. "📦 headphones: Ready for collection (11.11 14:02)\\n   WRO118M, Kościuszki 70"."""
    if p.status is None:
        return f"📦 {name(p)}: not in InPost's system yet"
    when = f" ({p.status_at.astimezone(tz):%d.%m %H:%M})" if p.status_at else ""
    line = f"📦 {name(p)}: {p.title or p.status}{when}"
    if p.locker and p.status not in DONE:
        line += f"\n   {p.locker}"
    return line
