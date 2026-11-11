"""The week's dinner ideas. No network here."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.modules.meals.models import MealIdea

IDEAS = 3


def week_of(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


def ideas(session: Session, today: date) -> list[MealIdea]:
    """This week's ideas (none until the Monday night run made them)."""
    q = select(MealIdea).where(MealIdea.week == week_of(today)).order_by(MealIdea.idx)
    return list(session.scalars(q))


def parse(answer: str) -> list[tuple[str, str, str]]:
    """Answer lines "name | uses | how" → up to IDEAS ideas; other lines are ignored."""
    found = []
    for line in answer.splitlines():
        parts = [p.strip(" •-*`") for p in line.split("|")]
        if len(parts) == 3 and all(parts) and len(found) < IDEAS:
            found.append((parts[0], parts[1], parts[2]))
    return found


def format_idea(m: MealIdea) -> str:
    return f"🍳 {m.name}\n   uses: {m.uses}\n   {m.how}"
