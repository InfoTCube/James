"""Finding your products in the stored leaflet text. No network here.

Matching happens on read, so a product you add is found in leaflets that were already read.
"""

import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from assistant.core.config import fold
from assistant.modules.biedronka.models import BiedronkaPage, BiedronkaProduct

# Of the page's width: a price this close to a line naming the product belongs to it. Further for
# a line with a size ("…, 200 g"), which is the product's name rather than words on its packaging.
NEAR = 0.33
NEAR_SIZED = 0.45
TALL = 2.5  # times the page's usual line height: how much bigger the main price is printed
SIZE = re.compile(r"\d\s*(g|kg|ml|l|szt)\b", re.I)
WHOLE_PRICE = re.compile(r"^\d{3,4}$")  # printed big without a comma: "299" is 2,99 zł
COMMA_PRICE = re.compile(r"^\d{1,3},\d{2}$")
DISCOUNT = re.compile(r"(\d{1,2})\s*%\s*TAN", re.I)


@dataclass
class Offer:
    product: str  # your name for it
    text: str  # the leaflet's words, as OCR read them
    price: float | None
    old_price: float | None
    discount: int | None  # percent
    valid_to: date | None
    page_id: int
    image: str


def add_products(session: Session, names: list[str], now: datetime) -> list[str]:
    """Watch these products (adding one twice is fine); the cleaned-up names."""
    cleaned = list(dict.fromkeys(" ".join(n.split()) for n in names if n.strip()))
    for name in cleaned:
        session.merge(BiedronkaProduct(key=fold(name), name=name, added_at=now))
    return cleaned


def remove_product(session: Session, name: str) -> bool:
    """Stop watching a product; False if it wasn't watched."""
    row = session.get(BiedronkaProduct, fold(" ".join(name.split())))
    if row is None:
        return False
    session.delete(row)
    return True


def products(session: Session) -> list[str]:
    """The products you're watching, alphabetically."""
    return list(session.scalars(select(BiedronkaProduct.name).order_by(BiedronkaProduct.key)))


def normalize(text: str) -> str:
    """Folded, with t read as l: OCR often reads "ł" as "t" ("Masto" for "Masło")."""
    return fold(text).replace("t", "l")


def word_matches(word: str, wanted: str) -> bool:
    """`wanted` starts this word ("ser" in "serek", not in "deser"). Longer ones may have one
    misread letter, and may run into the next word ("masfoekslra" for "masło")."""
    if word.startswith(wanted):
        return True
    return len(wanted) >= 4 and SequenceMatcher(None, word[: len(wanted)], wanted).ratio() >= 0.8


def line_matches(line: str, product: str) -> bool:
    """Every word of the product name is found in the line."""
    words = re.findall(r"[a-z0-9]+", normalize(line))
    return all(any(word_matches(w, p) for w in words) for p in normalize(product).split())


def _price(text: str, height: float, tall: float) -> float | None:
    """The value of a big price line, or None if it isn't one."""
    text = text.strip()
    if height < tall:
        return None
    if WHOLE_PRICE.match(text):
        return int(text) / 100
    return float(text.replace(",", ".")) if COMMA_PRICE.match(text) else None


def find_on_page(lines: list[list], product: str) -> list[dict]:
    """The offers for a product on one page, judged by where things are printed: each big price
    gets the nearest line naming the product, then the nearest discount and old price.

    ponytail: layout heuristic. A product word in another product's name counts ("mleko" in
    "Mlekovita") and a price can be missed; each offer links its page image for checking.
    """
    if not lines:
        return []
    width = max(x for x, _, _, _ in lines) or 1
    tall = TALL * statistics.median(h for _, _, h, _ in lines)

    def dist(a: list, b: list) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    found = [ln for ln in lines if line_matches(ln[3], product)]
    prices = [(ln, p) for ln in lines if (p := _price(ln[3], ln[2], tall)) is not None]
    offers: dict[float | str, dict] = {}
    for name in sorted(found, key=lambda ln: not SIZE.search(ln[3])):  # real names first
        sized = SIZE.search(name[3])
        near = width * (NEAR_SIZED if sized else NEAR)
        close = [(dist(name, ln), ln, p) for ln, p in prices if dist(name, ln) <= near]
        if not close:
            if sized:  # a named product with a price OCR missed
                offers.setdefault(name[3], {"text": name[3], "price": None, "old_price": None,
                                            "discount": None})  # fmt: skip
            continue
        _, spot, price = min(close, key=lambda c: c[0])
        if price in offers:
            continue
        discounts = [(dist(spot, ln), m) for ln in lines if (m := DISCOUNT.search(ln[3]))]
        labels = [ln for ln in lines if "obni" in fold(ln[3])]
        old = None
        if labels:
            label = min(labels, key=lambda ln: dist(spot, ln))
            values = [ln for ln in lines if COMMA_PRICE.match(ln[3].strip()) and ln is not spot]
            if values and dist(spot, label) <= width * NEAR_SIZED:
                old = min(values, key=lambda ln: dist(label, ln))[3].strip().replace(",", ".")
        offers[price] = {
            "text": name[3],
            "price": price,
            "old_price": float(old) if old else None,
            "discount": int(min(discounts, key=lambda d: d[0])[1][1]) if discounts else None,
        }
    return list(offers.values())


def offers(session: Session, today: date) -> list[Offer]:
    """Your products in the leaflets valid today or coming up, by product and then price."""
    names = products(session)
    if not names:
        return []
    pages = session.scalars(
        select(BiedronkaPage)
        .where(BiedronkaPage.lines.is_not(None))
        .where(or_(BiedronkaPage.valid_to.is_(None), BiedronkaPage.valid_to >= today))
    )
    found: dict[tuple, Offer] = {}  # once, even if two leaflets share the page
    for page in pages:
        lines = json.loads(page.lines)
        for product in names:
            for o in find_on_page(lines, product):
                found.setdefault(
                    (product, page.image, o["price"], o["text"]),
                    Offer(product=product, valid_to=page.valid_to, page_id=page.id,
                          image=page.image, **o),
                )  # fmt: skip
    return sorted(found.values(), key=lambda o: (fold(o.product), o.price is None, o.price or 0))


def format_offer(o: Offer) -> str:
    """One line for the offer, then the leaflet's words and the page image to check it against."""
    head = f"🐞 {o.product}"
    if o.price is not None:
        head += f": {o.price:.2f} zł".replace(".", ",")
    extras = []
    if o.discount:
        extras.append(f"-{o.discount}%")
    if o.old_price:
        extras.append(f"was {o.old_price:.2f}".replace(".", ","))
    if o.valid_to:
        extras.append(f"until {o.valid_to:%d.%m}")
    if extras:
        head += " (" + ", ".join(extras) + ")"
    return f"{head}\n{o.text}\n{o.image}"
