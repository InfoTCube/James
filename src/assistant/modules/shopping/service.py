"""The shopping list, with items on Biedronka promotion flagged. No network here."""

from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.core.config import fold
from assistant.modules.biedronka import service as biedronka
from assistant.modules.shopping.models import ShoppingItem


def add(session: Session, texts: list[str], now: datetime) -> list[str]:
    """Add items (one already on the list isn't added twice); the ones added."""
    have = {fold(i.text) for i in items(session)}
    added = []
    for text in (" ".join(t.split()) for t in texts):
        if text and fold(text) not in have:
            session.add(ShoppingItem(text=text, added_at=now))
            have.add(fold(text))
            added.append(text)
    session.flush()
    return added


def items(session: Session) -> list[ShoppingItem]:
    """The list, in the order things were added."""
    return list(session.scalars(select(ShoppingItem).order_by(ShoppingItem.id)))


def remove(session: Session, ref: str) -> ShoppingItem | None:
    """Tick off an item by its number or its name (case/diacritics ignored, start of it is
    enough: "mle" for "mleko"); the item, or None."""
    ref = ref.strip().lstrip("#")
    for item in items(session):
        if (ref.isdigit() and item.id == int(ref)) or (
            ref and fold(item.text).startswith(fold(ref))
        ):
            session.delete(item)
            session.flush()
            return item
    return None


def clear(session: Session) -> int:
    """Empty the list; how many items there were."""
    return session.execute(delete(ShoppingItem)).rowcount


def promos(session: Session, today: date) -> dict[int, biedronka.Offer]:
    """Item id → the cheapest Biedronka offer that matches it (by the item's words, like the
    leaflet matching). Only offers already found for your /buy products: add a product there
    to have the leaflets searched for it."""
    offers = [o for o in biedronka.offers(session, today) if o.price is not None]
    found = {}
    for item in items(session):
        hits = [o for o in offers if biedronka.line_matches(f"{o.product} {o.text}", item.text)]
        if hits:
            found[item.id] = min(hits, key=lambda o: o.price)
    return found


def format_list(session: Session, today: date) -> list[str]:
    """Lines like "3. mleko  🐞 2,99 zł until 14.11"."""
    deals = promos(session, today)
    lines = []
    for item in items(session):
        line = f"{item.id}. {item.text}"
        if o := deals.get(item.id):
            line += f"  🐞 {o.price:.2f} zł".replace(".", ",")
            if o.valid_to:
                line += f" until {o.valid_to:%d.%m}"
        lines.append(line)
    return lines
