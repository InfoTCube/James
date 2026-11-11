"""Food leaflets from biedronka.pl (images only) → text by local OCR → alerts on your products."""

import json
import logging
import re
import time
from datetime import date
from functools import lru_cache

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import SentNotification, utcnow
from assistant.core.notifier import notify
from assistant.modules.biedronka import service
from assistant.modules.biedronka.models import BiedronkaPage

log = logging.getLogger(__name__)

LEAFLETS = "https://www.biedronka.pl/pl/gazetki"
API = "https://leaflet-api.prod.biedronka.cloud/api/leaflets/{}?ctx=web"
HEADERS = {"User-Agent": "Mozilla/5.0 (personal home assistant; a few requests a day)"}
# The food leaflets; the others are homeware, clothing and alcohol.
PRESS = re.compile(
    r"https://www\.biedronka\.pl/pl/press,id,(\w+),title,[\w-]*codziennie-niskie-ceny"
)
MAX_PAGES = 60  # per run, so one run stays short; the next hour carries on
MAX_ALERT = 15  # offers in one Telegram message


def leaflet_links(html: str) -> dict[str, str]:
    """The leaflets page → {press id: its URL}. Raises if there are none: the layout changed."""
    found = {m[1]: m[0] for m in PRESS.finditer(html)}
    if not found:
        raise ValueError("biedronka.pl: no food leaflets found in the page (layout changed?)")
    return found


def leaflet_uuid(html: str) -> str:
    """A leaflet's page → the id its images are listed under."""
    m = re.search(r'galleryLeaflet\.init\("([0-9a-f-]{36})"\)', html)
    if not m:
        raise ValueError("biedronka.pl: no leaflet id in the page (layout changed?)")
    return m[1]


def page_images(data: dict) -> list[str]:
    """The leaflet API's response → the page images, without inserted adverts."""
    urls = (u for spread in data["images_desktop"] for u in spread["images"] if u)
    return [u for u in dict.fromkeys(urls) if "_INS_" not in u]


def valid_dates(lines: list[list], today: date) -> tuple[date | None, date | None]:
    """The dates printed on a page ("2.11-7.11"). A range across New Year ends next year; any
    other that ends before it starts was misread, as are impossible dates."""
    for _, _, _, text in lines:
        if m := re.search(r"(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})", text):
            d1, m1, d2, m2 = map(int, m.groups())
            try:
                year = today.year - (today.month == 1 and m1 == 12)
                start, end = date(year, m1, d1), date(year + (m1 == 12 and m2 == 1), m2, d2)
            except ValueError:
                continue
            if start <= end:
                return start, end
    return None, None


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr_onnxruntime import RapidOCR  # slow to load; only this collector needs it

    return RapidOCR()


def read_page(image: bytes) -> list[list]:
    """A page image → [[x, y, height, text]] per line of text (x, y: its centre, in pixels)."""
    result, _ = _ocr()(image)
    rows = []
    for box, text, _score in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        rows.append([round(sum(xs) / 4), round(sum(ys) / 4), round(max(ys) - min(ys)), text])
    return rows


def get(url: str) -> httpx.Response:
    return httpx.get(url, headers=HEADERS, timeout=60, follow_redirects=True).raise_for_status()


def send_alert(session: Session, today: date) -> None:
    """One Telegram message with the offers not sent yet."""
    new = {}
    for o in service.offers(session, today):
        key = f"biedronka:{o.product}:{o.image}:{o.price}"[:200]
        if session.get(SentNotification, key) is None:
            new[key] = o
    if not new:
        return
    shown = [service.format_offer(o) for o in list(new.values())[:MAX_ALERT]]
    if len(new) > MAX_ALERT:
        shown.append(f"…and {len(new) - MAX_ALERT} more: /promos")
    if notify(session, "\n\n".join(shown)):
        session.add_all(SentNotification(key=key, sent_at=utcnow()) for key in new)


class BiedronkaCollector:
    """Hourly at night, until the current food leaflets are read (about 100 pages each; pages
    already read are kept). Off until you've added a product with /buy."""

    name = "biedronka"
    schedule = CronTrigger(hour="1-5", minute=35)

    def run(self, session: Session) -> None:
        now = utcnow()
        today = now.astimezone(load_config().tz).date()
        if not service.products(session):
            return
        links = leaflet_links(get(LEAFLETS).text)
        known = set(session.scalars(select(BiedronkaPage.press_id)))
        new_pages = []
        for press_id, url in links.items():
            if press_id not in known:
                uuid = leaflet_uuid(get(url).text)
                images = page_images(get(API.format(uuid)).json())
                new_pages += [BiedronkaPage(press_id=press_id, image=u) for u in images]
        todo = list(
            session.scalars(
                select(BiedronkaPage)
                .where(BiedronkaPage.lines.is_(None))
                .where(BiedronkaPage.press_id.in_(links))
            )  # fmt: skip
        )
        todo = (todo + new_pages)[:MAX_PAGES]
        failed = 0
        for page in todo:  # before writing: this takes minutes
            try:
                time.sleep(1)
                lines = read_page(get(page.image).content)
            except Exception:  # the next run tries again
                log.exception("biedronka: %s failed", page.image)
                failed += 1
                continue
            page.lines = json.dumps(lines, ensure_ascii=False)
            page.valid_from, page.valid_to = valid_dates(lines, today)
        if todo and failed == len(todo):
            raise RuntimeError("biedronka: no leaflet page could be read")
        session.add_all(new_pages)
        session.execute(delete(BiedronkaPage).where(BiedronkaPage.press_id.not_in(links)))
        session.flush()
        send_alert(session, today)
