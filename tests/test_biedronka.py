import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.core.db import make_engine
from assistant.modules.biedronka import collector, service
from assistant.modules.biedronka.models import BiedronkaPage
from assistant.services.bot.replies import reply_buy, reply_buy_del, reply_promos

FIXTURES = Path(__file__).parent / "fixtures" / "biedronka"
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
TODAY = date(2026, 11, 2)
CONFIG = Config(location={"latitude": 51.1, "longitude": 17.0})
BUTTER = json.loads((FIXTURES / "page_butter.json").read_text(encoding="utf-8"))
BREAD = json.loads((FIXTURES / "page_bread.json").read_text(encoding="utf-8"))
GAZETKI = (FIXTURES / "gazetki.html").read_text(encoding="utf-8")
PRESS = (FIXTURES / "press.html").read_text(encoding="utf-8")
LEAFLET = json.loads((FIXTURES / "leaflet.json").read_text(encoding="utf-8"))
ID_NOW, ID_OLD = "j2ot4ry84", "j2kjp4ji9"


def test_leaflet_links_are_only_the_food_ones():
    assert set(collector.leaflet_links(GAZETKI)) == {ID_NOW, ID_OLD}
    with pytest.raises(ValueError):
        collector.leaflet_links("<html>redesigned</html>")


def test_leaflet_uuid_and_images():
    assert collector.leaflet_uuid(PRESS) == "c1d6acea-aab5-453f-9ce1-18a1fa379005"
    with pytest.raises(ValueError):
        collector.leaflet_uuid("<html></html>")
    images = collector.page_images(LEAFLET)
    assert images and all("_INS_" not in u and u for u in images)  # no blanks or adverts


def test_valid_dates():
    assert collector.valid_dates(BUTTER, TODAY) == (date(2026, 11, 2), date(2026, 11, 7))
    assert collector.valid_dates(BREAD, TODAY) == (date(2026, 11, 2), date(2026, 11, 7))
    across = [[0, 0, 0, "29.12-3.01"]]
    assert collector.valid_dates(across, date(2026, 12, 28)) == (
        date(2026, 12, 29),
        date(2027, 1, 3),
    )
    assert collector.valid_dates(across, date(2027, 1, 2)) == (date(2026, 12, 29), date(2027, 1, 3))
    assert collector.valid_dates([[0, 0, 0, "99.99-99.99"]], TODAY) == (None, None)


def test_matching_survives_ocr_misreads():
    assert service.line_matches("Masto Ekstra Polskie Mlekovita, 200 g", "masło")  # ł read as t
    assert service.line_matches("MasfoEkstraMleczna", "masło")  # ł as f, words run together
    assert service.line_matches("MaSLO EKSTr", "Masło")
    assert service.line_matches("Serek wiejski", "ser")
    assert not service.line_matches("Deser czekoladowy", "ser")
    assert not service.line_matches("Kasa", "kawa")
    assert service.line_matches("Masto Ekstra", "masło ekstra")
    assert not service.line_matches("Masto Ekstra", "masło roślinne")


def test_finds_both_butters_with_their_prices_on_a_real_page():
    offers = {o["price"]: o for o in service.find_on_page(BUTTER, "masło")}
    assert set(offers) == {2.99, 3.99}
    assert (offers[2.99]["discount"], offers[2.99]["old_price"]) == (40, 4.99)
    assert (offers[3.99]["discount"], offers[3.99]["old_price"]) == (66, 5.99)


def test_finds_a_product_with_no_discount_on_a_real_page():
    [offer] = service.find_on_page(BREAD, "schiacciata")
    assert (offer["price"], offer["discount"], offer["old_price"]) == (2.49, None, None)


def test_words_on_packaging_do_not_take_a_price():
    offers = service.find_on_page(BUTTER, "mleko")  # MLEKO is printed on the butter's wrapper
    assert [o["text"] for o in offers] == ["Masto Ekstra Polskie Mlekovita, 200 g"]  # known limit


def test_misread_dates_are_not_believed():
    assert collector.valid_dates([[0, 0, 0, "12.11-7.11"]], TODAY) == (None, None)
    two = [[0, 0, 0, "12.11-7.11"], [0, 0, 0, "2.11-7.11"]]
    assert collector.valid_dates(two, TODAY) == (date(2026, 11, 2), date(2026, 11, 7))


def test_a_product_not_on_the_page_finds_nothing():
    assert service.find_on_page(BUTTER, "kawa") == []
    assert service.find_on_page([], "kawa") == []


def test_format_offer():
    o = service.Offer("masło", "Masto Ekstra", 2.99, 4.99, 40, date(2026, 11, 7), 1, "https://i/1")
    assert service.format_offer(o) == (
        "🐞 masło: 2,99 zł (-40%, was 4,99, until 07.11)\nMasto Ekstra\nhttps://i/1"
    )
    plain = service.Offer("kawa", "Kawa, 250 g", None, None, None, None, 1, "https://i/2")
    assert service.format_offer(plain) == "🐞 kawa\nKawa, 250 g\nhttps://i/2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "db.sqlite")
    fetched, sent, ocr = [], [], {"lines": BUTTER}

    def get(url):
        fetched.append(url)
        request = httpx.Request("GET", url)
        if url == collector.LEAFLETS:
            return httpx.Response(200, text=env_.gazetki, request=request)
        if "leaflet-api" in url:
            return httpx.Response(200, json=LEAFLET, request=request)
        if "/press," in url:
            return httpx.Response(200, text=PRESS, request=request)
        return httpx.Response(200, content=b"png", request=request)

    def read_page(image):
        if isinstance(ocr["lines"], Exception):
            raise ocr["lines"]
        return ocr["lines"]

    monkeypatch.setattr(collector, "get", get)
    monkeypatch.setattr(collector, "read_page", read_page)
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text) or True)
    monkeypatch.setattr(collector, "load_config", lambda: CONFIG)
    monkeypatch.setattr(collector, "utcnow", lambda: NOW)

    class Env:
        gazetki = GAZETKI

        def __init__(self):
            self.engine, self.fetched, self.sent, self.ocr = engine, fetched, sent, ocr

        def run(self, answer=None):
            """A night: read the pages, then the AI check (`answer`; None: the call failed)."""
            monkeypatch.setattr(collector.ai, "ask", lambda *a, **k: answer)
            monkeypatch.setattr(collector.ai, "at_night", lambda now: True)
            with Session(engine) as s, s.begin():
                collector.BiedronkaCollector().run(s)
            with Session(engine) as s, s.begin():
                collector.BiedronkaCheckCollector().run(s)

        def buy(self, *names):
            with Session(engine) as s, s.begin():
                service.add_products(s, list(names), NOW)

        def pages(self):
            with Session(engine) as s:
                return list(s.scalars(select(BiedronkaPage)))

    env_ = Env()
    return env_


def test_off_until_a_product_is_added(env):
    env.run()
    assert env.fetched == [] and env.pages() == []


def test_run_reads_pages_alerts_once_and_is_idempotent(env):
    env.buy("masło")
    env.run()
    pages = env.pages()
    assert len(pages) == 2 * len(collector.page_images(LEAFLET))  # two leaflets, same fixture
    assert all(p.lines for p in pages) and pages[0].valid_to == date(2026, 11, 7)
    with Session(env.engine) as s:  # a shared page's offers appear once per page image
        offers = service.offers(s, TODAY)
    assert len({(o.image, o.price) for o in offers}) == len(offers)
    assert len(env.sent) == 1 and "🐞 masło: 2,99 zł (-40%, was 4,99, until 07.11)" in env.sent[0]
    env.fetched.clear()
    env.run()
    assert len(env.sent) == 1  # same offers: no second alert
    assert not [u for u in env.fetched if u.endswith(".png")]  # nothing read twice


def test_a_new_product_is_found_in_pages_already_read(env):
    env.buy("masło")
    env.run()
    env.buy("mleko")
    with Session(env.engine) as s:
        assert {o.product for o in service.offers(s, TODAY)} == {"masło", "mleko"}


def test_limit_per_run_and_failures_are_retried(env, monkeypatch):
    monkeypatch.setattr(collector, "MAX_PAGES", 2)
    env.buy("masło")
    env.run()
    assert sum(p.lines is not None for p in env.pages()) == 2
    env.ocr["lines"] = RuntimeError("ocr broke")
    with pytest.raises(RuntimeError):  # nothing could be read
        env.run()
    env.ocr["lines"] = BUTTER
    env.run()
    assert sum(p.lines is not None for p in env.pages()) == 4


def test_expired_offers_and_removed_leaflets_go(env):
    env.buy("masło")
    env.run()
    with Session(env.engine) as s:
        assert service.offers(s, date(2026, 11, 8)) == []  # after 7.11
    env.gazetki = GAZETKI.replace(ID_NOW, "other").replace(ID_OLD, "other2")
    env.ocr["lines"] = BREAD
    env.run()
    assert {p.press_id for p in env.pages()} == {"other", "other2"}


def test_buy_commands(env):
    with Session(env.engine) as s, s.begin():
        assert reply_buy(s, NOW, CONFIG, []).startswith("No products yet")
        assert reply_buy(s, NOW, CONFIG, ["masło,", "mleko", "3,2%,", "kawa"]) == (
            "🐞 Watching: masło, mleko 3,2%, kawa"
        )
        s.flush()
        assert reply_buy(s, NOW, CONFIG, []) == "🐞 Watching: kawa, masło, mleko 3,2%"
        assert reply_buy_del(s, NOW, CONFIG, ["KAWA"]) == "Stopped watching KAWA."
        s.flush()
        assert reply_buy_del(s, NOW, CONFIG, ["kawa"]) == "kawa isn't on your list."


def test_promos_reply(env):
    with Session(env.engine) as s:
        assert reply_promos(s, NOW, CONFIG, []).startswith("No products yet")
    env.buy("kawa")
    with Session(env.engine) as s:
        assert reply_promos(s, NOW, CONFIG, []).startswith("None of your products")
    env.buy("masło")
    env.run()
    with Session(env.engine) as s:
        assert reply_promos(s, NOW, CONFIG, []).startswith("🐞 masło: 2,99 zł")


def test_widget_and_tap(env, monkeypatch):
    from fastapi.testclient import TestClient

    from assistant.modules.biedronka import routes
    from assistant.services.api.app import app

    monkeypatch.setattr(routes, "get_engine", lambda: env.engine)
    monkeypatch.setattr(routes, "utcnow", lambda: NOW + timedelta(hours=1))
    client = TestClient(app)
    assert client.get("/widgets/biedronka").text == ""
    env.buy("masło")
    env.run()
    html = client.get("/widgets/biedronka").text
    assert "2,99" in html and "-40%" in html and "was 4,99" in html
    page_id = html.split('hx-post="/api/biedronka/send/')[1].split('"')[0]
    sent = []
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    assert client.post(f"/api/biedronka/send/{page_id}").text == "✓ sent to Telegram"
    assert sent[0].startswith("🐞 masło") and "https://images.biedronka.cloud" in sent[0]


def test_ai_check_hides_wrong_offers_fixes_prices_and_runs_once(env, monkeypatch):
    env.buy("masło")
    with Session(env.engine) as s, s.begin():  # read pages without the check
        monkeypatch.setattr(collector.ai, "ask", lambda *a, **k: None)
        collector.BiedronkaCollector().run(s)
    with Session(env.engine) as s:
        todo = service.offers(s, TODAY)
    assert len(todo) >= 2 and not any(o.checked for o in todo)
    data = service.check_input(todo)
    assert data.startswith(f"1 | masło | {todo[0].text} | ") and todo[0].context in data

    asked = []

    def ask(module, prompt, data):
        asked.append(data)
        rest = "\n".join(f"{i} | ok" for i in range(3, len(todo) + 1))
        return f"1 | wrong\n2 | Price 1,49 zł\nsure, here you go\n{rest}\n99 | ok"

    env.sent.clear()
    monkeypatch.setattr(collector.ai, "ask", ask)
    with Session(env.engine) as s, s.begin():
        collector.BiedronkaCheckCollector().run(s)
    with Session(env.engine) as s:
        shown = service.offers(s, TODAY)
    assert todo[0].text not in [o.text for o in shown if o.image == todo[0].image]
    fixed = next(o for o in shown if o.image == todo[1].image and o.text == todo[1].text)
    assert (fixed.price, fixed.discount, fixed.checked) == (1.49, None, True)
    assert len(env.sent) == 1 and "1,49 zł" in env.sent[0]

    with Session(env.engine) as s, s.begin():  # checked offers aren't sent again
        collector.BiedronkaCheckCollector().run(s)
    assert len(asked) == 1


def test_ai_check_waits_for_the_night(env, monkeypatch):
    env.buy("masło")
    with Session(env.engine) as s, s.begin():
        monkeypatch.setattr(collector.ai, "ask", lambda *a, **k: None)
        monkeypatch.setattr(collector.ai, "at_night", lambda now: False)
        collector.BiedronkaCollector().run(s)
        collector.BiedronkaCheckCollector().run(s)
    assert env.sent == []  # daytime start: no unchecked alert, the night run does it


def test_price_history_notes(tmp_path):
    from datetime import timedelta

    engine = make_engine(tmp_path / "db.sqlite")

    def offer(price, checked=True):
        o = service.Offer("Masło", "Masło Ekstra", price, None, None, None, 1, "https://i/1")
        o.checked = checked
        return o

    with Session(engine) as s:
        for days_ago, price in [(30, 6.49), (20, 5.99), (9, 6.29)]:
            service.record_prices(s, [offer(price)], TODAY - timedelta(days=days_ago))
        service.record_prices(s, [offer(1.00, checked=False)], TODAY)  # unchecked: not kept
        s.flush()
        cheap, usual = offer(5.49), offer(6.49)
        service.add_notes(s, [cheap, usual], TODAY)
        assert (cheap.note, usual.note) == ("lowest in 8 weeks", "8-week low 5,99")
        assert "lowest in 8 weeks)" in service.format_offer(cheap)

        young = offer(5.0)  # history shorter than 2 weeks says nothing
        service.add_notes(s, [young], TODAY - timedelta(days=25))
        assert young.note == ""
