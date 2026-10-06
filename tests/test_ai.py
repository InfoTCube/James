import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core import ai
from assistant.core.db import AiCall, make_engine

NIGHT = datetime(2026, 10, 7, 1, 30, tzinfo=UTC)  # 03:30 in Warsaw
DAY = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)  # 14:00


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Fake CLI: records each call; `fake.reply` is what it prints (or an exception to raise)."""
    engine = make_engine(tmp_path / "db.sqlite")
    monkeypatch.setattr(ai, "get_engine", lambda: engine)
    monkeypatch.setattr(ai, "utcnow", lambda: NIGHT)
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/bin/claude")
    calls = []

    def run(cmd, input, cwd, timeout, **kw):
        calls.append({"cmd": cmd, "input": input, "cwd": cwd, "files": list(Path(cwd).iterdir())})
        if isinstance(fake.reply, Exception):
            raise fake.reply
        return subprocess.CompletedProcess(cmd, fake.code, stdout=fake.reply, stderr="boom")

    monkeypatch.setattr(ai.subprocess, "run", run)
    ok = json.dumps({"is_error": False, "result": "Wrocław"})
    fake = type("Fake", (), {"calls": calls, "engine": engine, "code": 0, "reply": ok})
    return fake


def recorded(engine) -> list[AiCall]:
    with Session(engine) as s:
        return list(s.scalars(select(AiCall).order_by(AiCall.id)))


def test_ask_pipes_data_into_a_bare_claude(fake):
    assert ai.ask("news", "First city?", "Wrocław, Kraków") == "Wrocław"
    call = fake.calls[0]
    assert call["cmd"][:3] == ["/bin/claude", "-p", "First city?"]
    assert call["input"] == "Wrocław, Kraków"  # data on stdin, not in the arguments
    assert call["files"] == []  # empty working directory: no project context
    flags = call["cmd"]
    for flag, value in [("--output-format", "json"), ("--max-turns", "1"), ("--tools", ""),
                        ("--setting-sources", ""), ("--model", "haiku")]:  # fmt: skip
        assert flags[flags.index(flag) + 1] == value
    assert "--strict-mcp-config" in flags
    [row] = recorded(fake.engine)
    assert (row.module, row.result, row.error) == ("news", "Wrocław", None)


def test_same_input_is_answered_from_the_cache(fake):
    ai.ask("news", "First city?", "Wrocław, Kraków")
    assert ai.ask("films", "First city?", "Wrocław, Kraków") == "Wrocław"
    assert len(fake.calls) == 1
    ai.ask("news", "First city?", "Wrocław, Kraków", model="sonnet")  # different model: new call
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "reply, code, error",
    [
        (json.dumps({"is_error": True, "result": "usage limit reached"}), 0, "usage limit"),
        ("Invalid API key", 1, "exit 1"),
        (subprocess.TimeoutExpired("claude", 180), 0, "TimeoutExpired"),
    ],
)
def test_failures_return_none_and_are_recorded(fake, reply, code, error):
    fake.reply, fake.code = reply, code
    assert ai.ask("news", "Summarise", "x") is None
    [row] = recorded(fake.engine)
    assert row.result is None and error in row.error
    fake.reply, fake.code = json.dumps({"is_error": False, "result": "ok"}), 0
    assert ai.ask("news", "Summarise", "x") == "ok"  # failures aren't cached


def test_no_cli(fake, monkeypatch):
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)
    assert ai.ask("news", "Summarise", "x") is None
    assert "claude CLI not found" in recorded(fake.engine)[0].error


def test_daily_cap(fake, monkeypatch):
    monkeypatch.setattr(ai, "MAX_CALLS_PER_DAY", 2)
    assert ai.ask("news", "a", "x") and ai.ask("news", "b", "x")
    assert ai.ask("news", "c", "x") is None
    assert len(fake.calls) == 2 and "daily cap" in recorded(fake.engine)[-1].error
    assert ai.ask("news", "a", "x") == "Wrocław"  # cached answers still work


def test_only_at_night_unless_it_cannot_wait(fake, monkeypatch):
    assert ai.ask("news", "a", "x") == "Wrocław"  # 03:30: called
    monkeypatch.setattr(ai, "utcnow", lambda: DAY)
    assert ai.ask("news", "b", "x") is None  # 14:00: not called
    assert len(fake.calls) == 1 and len(recorded(fake.engine)) == 1  # skipped: not recorded
    assert ai.ask("news", "a", "x") == "Wrocław"  # cached answers any time
    assert ai.ask("voice", "b", "x", anytime=True) == "Wrocław"
    assert len(fake.calls) == 2


def test_night_window_edges():
    at = lambda h, m: datetime(2026, 1, 15, h, m, tzinfo=UTC)  # noqa: E731  winter: UTC+1
    assert [ai.at_night(at(*t)) for t in [(23, 59), (0, 0), (4, 59), (5, 0)]] == [
        False, True, True, False
    ]  # fmt: skip
