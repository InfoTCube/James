import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from assistant.core.config import Config, JobsConfig
from assistant.core.db import make_engine
from assistant.modules.jobs import collector, service
from assistant.modules.jobs.models import JobOffer
from assistant.services.bot.replies import reply_jobs

FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def load(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text if name.endswith(".html") else json.loads(text)


def offer(**kw) -> JobOffer:
    base = dict(
        id="x:1", source="x", title="Junior .NET Developer", company="Acme", url="u",
        salary=None, seniority="junior", skills="C#, ASP.NET Core", cities="Wrocław",
        remote=False, hybrid=False, office_days=None, full_time=None, published=NOW,
    )  # fmt: skip
    return JobOffer(**base | kw)


def test_parse_justjoin():
    offers = collector.parse_justjoin(load("justjoin.json"))
    assert len(offers) == 12
    o = offers[5]
    assert o.id == "justjoin:simcorp-senior-software-engineer-net--warszawa-net"
    assert (
        o.url == "https://justjoin.it/job-offer/simcorp-senior-software-engineer-net--warszawa-net"
    )
    assert (o.seniority, o.cities, o.remote, o.hybrid, o.office_days) == (
        "senior", "Warszawa", False, True, 2
    )  # fmt: skip
    assert o.published == datetime(2026, 10, 5, 13, 0, 10, tzinfo=UTC)
    assert offers[1].remote and offers[1].office_days is None
    assert offers[2].salary == "130–140 PLN/hour"  # not the monthly equivalent in from/to
    assert offers[3].salary == "20 000–30 000 PLN/month"
    assert (offers[0].full_time, offers[11].full_time) == (True, None)  # freelance: doesn't say


def test_parse_nofluffjobs():
    o = collector.parse_nofluffjobs(load("nofluffjobs.json"))[2]
    assert o.title == "Junior Software Engineer - .NET - Allegro Klik/Care"
    assert o.url.startswith("https://nofluffjobs.com/pl/job/junior-software-engineer-net-")
    assert (o.salary, o.seniority, o.cities) == ("11 200–15 250 PLN/month", "Junior", "Warszawa")
    assert (o.remote, o.hybrid, o.office_days) == (False, True, None)
    assert o.skills == ".NET, Microservices, AI"


def test_parse_pracuj():
    offers = collector.parse_pracuj(load("pracuj.html"))
    assert len(offers) == 10
    o = offers[5]
    assert (o.title, o.cities) == (".NET Backend Developer", "Łódź")
    assert (o.remote, o.hybrid) == (False, True)
    assert o.url == "https://www.pracuj.pl/praca/net-backend-developer-lodz,oferta,1005137170"
    assert "(senior)" in o.seniority and o.salary.startswith("120")
    assert o.skills == "C#, .NET Core, .NET, SQL"
    assert (offers[0].full_time, o.full_time) == (True, None)  # "Pełny etat"; none listed
    with pytest.raises(ValueError):
        collector.parse_pracuj("<html>blocked</html>")


def test_matches_keywords_and_level():
    cfg = JobsConfig(keywords=[".net", "c#", "asp.net"], exclude=["senior"], seniority=["junior"])
    assert service.matches(offer(), cfg)
    assert service.matches(offer(title="Młodszy programista", skills="C#"), cfg)  # skills count
    assert service.matches(offer(title="Programista", skills="ASP.NET MVC"), cfg)
    assert not service.matches(offer(title="Java Developer", skills="Spring, MariaDB.Network"), cfg)
    assert not service.matches(offer(title="Senior .NET Developer"), cfg)  # excluded word
    assert not service.matches(offer(seniority="Specjalista (mid / Regular)"), cfg)
    assert service.matches(offer(seniority=""), cfg)  # no level given → not ruled out


def test_matches_place():
    cfg = JobsConfig(keywords=["c#"], hybrid_cities=["Kraków", "Warszawa"], max_office_days=1)
    assert service.matches(offer(cities="WROCLAW"), cfg)  # case and diacritics ignored
    assert service.matches(offer(cities="Gdańsk", remote=True), cfg)
    assert not service.matches(
        offer(cities="Gdańsk", remote=True), cfg.model_copy(update={"remote": False})
    )  # noqa: E501
    assert not service.matches(offer(cities="Kraków"), cfg)  # office only
    assert service.matches(offer(cities="Kraków", hybrid=True, office_days=1), cfg)
    assert not service.matches(offer(cities="Kraków", hybrid=True, office_days=2), cfg)
    assert service.matches(offer(cities="Warszawa", hybrid=True), cfg)  # office days unknown
    assert not service.matches(offer(cities="Gdańsk", hybrid=True, office_days=1), cfg)


FIT_CFG = JobsConfig(
    keywords=[".net", "c#"], seniority=["junior"], hybrid_cities=["Kraków", "Warszawa"]
)


def test_format_offer():
    o = offer(
        salary="11 200 PLN/month", cities="Kraków, Wrocław, Łódź, Gdańsk", hybrid=True,
        full_time=True,
    )  # fmt: skip
    assert service.format_offer(o, FIT_CFG) == (
        "⭐ Junior .NET Developer – Acme\n"
        "11 200 PLN/month · Kraków, Wrocław, Łódź +1 · hybrid (office days?) · full-time\nu"
    )
    assert service.format_offer(offer(title="Junior Dev"), FIT_CFG).startswith("Junior Dev –")
    assert service.work_mode(offer(hybrid=True, office_days=1)) == "hybrid, 1 office day/week"
    assert service.hours(offer(full_time=False)) == "part-time OK"
    assert service.hours(offer()) is None


def test_fit_and_rank():
    assert service.fit(offer(), FIT_CFG) == 3
    assert service.fit(offer(title="Junior Developer"), FIT_CFG) == 2  # .NET only in skills
    assert service.fit(offer(cities="Kraków", hybrid=True), FIT_CFG) == 2  # the "maybe" place
    assert service.fit(offer(cities="Kraków", remote=True), FIT_CFG) == 3
    assert service.fit(offer(seniority=""), FIT_CFG) == 2  # level not stated
    no_level = FIT_CFG.model_copy(update={"seniority": []})
    assert service.best_fit(offer(seniority=""), no_level)
    older_best = offer(id="a", published=NOW.replace(day=1))
    newer = offer(id="b", title="Junior Developer")
    newest = offer(id="c", title="Junior Developer", published=NOW.replace(hour=13))
    assert service.rank([newer, older_best, newest], FIT_CFG) == [older_best, newest, newer]


@pytest.fixture
def run_jobs(tmp_path, monkeypatch):
    """Run the collector over fake boards {name: offers or an exception}; returns sent texts."""
    engine = make_engine(tmp_path / "db.sqlite")
    sent = []
    monkeypatch.setattr(collector, "utcnow", lambda: NOW)
    monkeypatch.setattr(collector, "notify", lambda s, text, key=None: sent.append(text))

    def fetcher(result):
        def fetch(cfg):
            if isinstance(result, Exception):
                raise result
            return result

        return fetch

    def run(cfg: Config, boards: dict) -> list[str]:
        monkeypatch.setattr(collector, "load_config", lambda: cfg)
        sources = {name: (name, fetcher(result)) for name, result in boards.items()}
        monkeypatch.setattr(collector, "SOURCES", sources)
        with Session(engine) as s, s.begin():
            collector.JobsCollector().run(s)
        return sent

    run.engine = engine
    return run


def test_collector_alerts_once(run_jobs):
    jobs = {"keywords": [".net"], "hybrid_cities": ["Kraków", "Warszawa"]}
    cfg = Config(location={"latitude": 51.1, "longitude": 17.0}, jobs=jobs)

    def boards():  # fresh objects each run, as from a real fetch
        return {"nofluffjobs": collector.parse_nofluffjobs(load("nofluffjobs.json"))}

    run_jobs(cfg, boards())
    sent = run_jobs(cfg, boards())
    assert len(sent) == 1  # second run: nothing new
    # 10 postings: 4 office-only (out), 2 hybrid, 4 remote = 2 jobs each listed twice
    assert sent[0].startswith("💼 4 new jobs\n\n")
    with Session(run_jobs.engine) as s:
        assert reply_jobs(s, NOW, cfg, []).count("https://nofluffjobs.com") == 4
        assert reply_jobs(s, NOW.replace(month=11), cfg, []).startswith("No matching")


def test_dedupe_key():
    def key(title: str, company: str) -> str:
        return service.dedupe_key(offer(title=title, company=company))

    same = [
        (("Fullstack Developer (.NET + Angular)", "Scalo Sp. z o.o."),
         ("Fullstack Developer (Angular+.NET)", "SCALO")),
        (("Senior .NET Engineer with AI", "EPAM Systems (Poland) sp. z o.o."),
         ("Senior .NET Engineer with AI", "EPAM Systems")),
        (("Junior SharePoint Developer (k/m/n)", "Nexio"),
         ("Junior SharePoint Developer", "Nexio")),
        ((".NET Developer", "Axito spółka z ograniczoną odpowiedzialnością"),
         (".NET Developer", "AXITO Sp. z o.o.")),
    ]  # fmt: skip
    for a, b in same:
        assert key(*a) == key(*b)
    different = [
        (("C# Developer", "Acme"), ("Developer", "Acme")),
        (("Software Engineer 1 (.NET)", "Allegro"), ("Software Engineer 2 (.NET)", "Allegro")),
        ((".NET Developer", "Scalo"), (".NET Developer", "Cyclad")),
    ]
    for a, b in different:
        assert key(*a) != key(*b)


def test_one_message_for_all_boards_without_duplicates(run_jobs):
    cfg = Config(location={"latitude": 51.1, "longitude": 17.0}, jobs={"keywords": [".net"]})
    sent = run_jobs(
        cfg,
        {
            "justjoin": [
                offer(id="justjoin:a", company="Scalo"),
                offer(id="justjoin:b", company="Scalo", cities="Wrocław, Łódź"),  # per city
            ],
            "pracuj": [
                offer(id="pracuj:a", company="Scalo Sp. z o.o.", title="Junior .NET Dev (k/m/n)"),
                offer(id="pracuj:b", company="Scalo Sp. z o.o.", title="Junior .NET Developer"),
            ],
        },
    )
    assert len(sent) == 1
    assert sent[0].startswith("💼 2 new jobs\n\n")
    assert sent[0].count("Junior .NET Developer – Scalo\n") == 1  # justjoin's, pracuj's dropped


def test_broken_board_does_not_stop_the_others(run_jobs):
    cfg = Config(location={"latitude": 51.1, "longitude": 17.0}, jobs={"keywords": [".net"]})
    sent = run_jobs(cfg, {"justjoin": ValueError("layout changed"), "pracuj": [offer()]})
    assert sent[0] == "⚠️ Jobs: justjoin failed: ValueError: layout changed"
    assert sent[1].startswith("💼 1 new job\n\n")
    with pytest.raises(RuntimeError):  # all failed → the run is recorded as an error
        run_jobs(cfg, {"justjoin": ValueError("x"), "pracuj": ValueError("y")})


def test_alert_fits_one_telegram_message():
    url = "https://justjoin.it/job-offer/acme-junior-net-developer-wroclaw-net-1a2b3c4d"
    many = [offer(id=f"x:{i}", title=f"Junior .NET Developer {i}", url=url) for i in range(60)]
    text = collector.alert_text(many, JobsConfig(keywords=[".net"]))
    assert len(text) <= collector.TELEGRAM_MAX
    assert text.startswith("💼 60 new jobs") and text.endswith("more in /jobs")


def test_tap_sends_the_offer_to_telegram(tmp_path, monkeypatch):
    from assistant.modules.jobs import routes

    engine = make_engine(tmp_path / "db.sqlite")
    with Session(engine) as s, s.begin():
        s.add(offer(id="nofluffjobs:junior-net-Wrocław", url="https://x/1", dedupe_key="k",
                    first_seen=NOW))  # fmt: skip
    sent = []
    monkeypatch.setattr(routes, "get_engine", lambda: engine)
    monkeypatch.setattr(
        routes, "load_config", lambda: Config(location={"latitude": 51.1, "longitude": 17.0})
    )  # noqa: E501
    monkeypatch.setattr(routes, "notify", lambda s, text: sent.append(text) or True)
    assert (
        routes.send_to_telegram("nofluffjobs:junior-net-Wrocław").body
        == "✓ sent to Telegram".encode()
    )
    assert sent[0].startswith("🔗 Junior .NET Developer – Acme") and sent[0].endswith("https://x/1")
    assert routes.send_to_telegram("pracuj:gone").body == "✗ gone".encode()
