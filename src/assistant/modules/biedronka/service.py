"""Finding your products in the stored leaflet text. No network here.

Matching happens on read, so a product you add is found in leaflets that were already read.
"""

import hashlib
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
from assistant.modules.biedronka.models import BiedronkaCheck, BiedronkaPage, BiedronkaProduct

# Of the page's width: a price this close to a line naming the product belongs to it. Further for
# a line with a size ("…, 200 g"), which is the product's name rather than words on its packaging.
NEAR = 0.33
NEAR_SIZED = 0.45
TALL = 2.5  # times the page's usual line height: how much bigger the main price is printed
SIZE = re.compile(r"\d\s*(g|kg|ml|l|szt)\b", re.I)
WHOLE_PRICE = re.compile(r"^\d{3,4}$")  # printed big without a comma: "299" is 2,99 zł
COMMA_PRICE = re.compile(r"^\d{1,3},\d{2}$")
DISCOUNT = re.compile(r"(\d{1,2})\s*%\s*TAN", re.I)
CONTEXT = 12  # lines around a price shown to the AI check


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
    context: str = ""  # OCR text printed around the price, for the AI check
    checked: bool = False  # Claude has seen it (wrong ones aren't returned at all)


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

    def around(spot: list) -> str:
        """The CONTEXT lines printed nearest to `spot`, top to bottom."""
        near = sorted(lines, key=lambda ln: dist(spot, ln))[:CONTEXT]
        return " / ".join(ln[3] for ln in sorted(near, key=lambda ln: (ln[1], ln[0])))

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
                                            "discount": None, "context": around(name)})  # fmt: skip
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
            "context": around(spot),
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
    checks = {c.key: c for c in session.scalars(select(BiedronkaCheck))}
    shown = []
    for o in found.values():
        c = checks.get(offer_key(o))
        if c and not c.ok:
            continue
        if c and c.price is not None and c.price != o.price:  # paired with a neighbour's price
            o.price, o.old_price, o.discount = c.price, None, None
        o.checked = c is not None
        shown.append(o)
    return sorted(shown, key=lambda o: (fold(o.product), o.price is None, o.price or 0))


def offer_key(o: Offer) -> str:
    """Identifies an offer across runs (same page, product, price as read, words)."""
    raw = json.dumps([o.product, o.image, o.price, o.text], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()


def check_input(todo: list[Offer]) -> str:
    """The offers for the AI check, one per line: id | wanted | matched words | price | nearby."""
    rows = []
    for i, o in enumerate(todo, 1):
        price = f"{o.price:.2f} zł".replace(".", ",") if o.price is not None else "no price"
        rows.append(f"{i} | {o.product} | {o.text} | {price} | {o.context}")
    return "\n".join(rows)


def parse_checks(answer: str, todo: list[Offer]) -> dict[str, tuple[bool, float | None]]:
    """Answer lines "id | ok", "id | wrong", "id | price 6,99" → {offer_key: (ok, price)}.
    Lines that don't parse are left out: those offers stay unchecked."""
    verdicts = {}
    for line in answer.splitlines():
        num, sep, verdict = line.partition("|")
        num, verdict = num.strip(" •-*`"), fold(verdict.strip(" .`"))
        if not sep or not num.isdigit() or not 1 <= int(num) <= len(todo):
            continue
        o = todo[int(num) - 1]
        if verdict == "ok":
            verdicts[offer_key(o)] = (True, None)
        elif verdict == "wrong":
            verdicts[offer_key(o)] = (False, None)
        elif m := re.fullmatch(r"price\s+(\d{1,3})[,.](\d{2})(\s*zl)?", verdict):
            verdicts[offer_key(o)] = (True, float(f"{m[1]}.{m[2]}"))
    return verdicts


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
