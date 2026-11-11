"""Weekly dinner ideas: Claude picks 3 that use this week's checked Biedronka offers."""

import logging

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.config import load_config
from assistant.core.db import utcnow
from assistant.modules.biedronka import service as biedronka
from assistant.modules.meals import service
from assistant.modules.meals.models import MealIdea

log = logging.getLogger(__name__)


def prompt(taste: str) -> str:
    return (
        "The input lists products on promotion this week at Biedronka (Polish names as printed "
        "in the leaflet, price, until when). Suggest 3 simple, different dinners for one person "
        "cooking at home that each use one or more of them, plus ordinary pantry things. "
        + (f"Their taste: {taste}. " if taste else "")
        + "Answer exactly 3 lines, 'dish name | promo products it uses | one sentence on how "
        "to make it'. Dish names in English. Nothing else."
    )


class MealsCollector:
    """Nightly after the Biedronka check; does its one call the first night of a week that has
    checked offers (retried next night if Claude fails)."""

    name = "meals"
    schedule = CronTrigger(hour=5, minute=55)  # after biedronka_check (05:50)

    def run(self, session: Session) -> None:
        config, now = load_config(), utcnow()
        today = now.astimezone(config.tz).date()
        if service.ideas(session, today) or not ai.at_night(now):
            return
        offers = [o for o in biedronka.offers(session, today) if o.checked and o.price]
        if not offers:
            return
        lines = [
            f"{o.text} ({o.product}) | {o.price:.2f} zł".replace(".", ",")
            + (f" | until {o.valid_to:%d.%m}" if o.valid_to else "")
            for o in offers
        ]
        answer = ai.ask("meals", prompt(config.meals.taste), "\n".join(lines))
        if answer is None:
            log.warning("meals: AI failed; retried next night")
            return
        week = service.week_of(today)
        for i, (name, uses, how) in enumerate(service.parse(answer)):
            session.add(MealIdea(week=week, idx=i, name=name, uses=uses, how=how))
