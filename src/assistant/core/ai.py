"""The only way to call Claude: the Claude Code CLI in headless mode, on the user's Pro plan.

    summary = ai.ask("news", "Summarise these headlines in 5 bullet points.", headlines)
    text = summary or headlines  # None on any failure: show the raw data instead

Each call pipes the data in, runs from an empty temp directory with no tools, MCP servers,
settings or hooks, and a one-line system prompt (~600 input tokens of overhead instead of ~7k).
The same input is answered from `ai_calls` instead of calling again. Every call is recorded there
(module, duration, result or error). Auth: CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`).

Claude is only called at night (NIGHT), so daily AI jobs don't use the Pro quota while the user
does: schedule them between 01:00 and 06:00. Calls that can't wait pass `anytime=True`.

Call it before writing in your transaction: it takes seconds to minutes and records the call in
its own short transaction.
"""

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from datetime import time as dt_time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant.core.config import load_config
from assistant.core.db import AiCall, get_engine, utcnow

log = logging.getLogger(__name__)

SYSTEM = "You process data for a personal home assistant. Use only the input you are given."
TIMEOUT = 180  # seconds
# ponytail: a global cap so a bug can't drain the Pro quota shared with the user's own use;
# per-module budgets if one module ever crowds out the others
MAX_CALLS_PER_DAY = 30
# Pro usage limits run in 5-hour windows from the first call: one opened at night is over before
# the user's day starts, so daily AI jobs run here unless they can't wait (`anytime=True`).
NIGHT = (dt_time(1, 0), dt_time(6, 0))  # local time
FLAGS = [
    "--output-format", "json",
    "--max-turns", "1",
    "--system-prompt", SYSTEM,
    "--tools", "",  # no tools: Claude can't fetch anything, it only sees the piped input
    "--strict-mcp-config",  # no MCP servers
    "--setting-sources", "",  # no user/project settings, hooks or CLAUDE.md
    "--disable-slash-commands",
    "--no-session-persistence",
]  # fmt: skip


def cache_key(prompt: str, data: str, model: str) -> str:
    return hashlib.sha256(json.dumps([prompt, data, model]).encode()).hexdigest()


def run_claude(prompt: str, data: str, model: str, timeout: int) -> str:
    """One headless call; the answer text. Raises on any failure."""
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI not found")
    with tempfile.TemporaryDirectory() as empty:  # no project context to load
        proc = subprocess.run(
            [exe, "-p", prompt, *FLAGS, "--model", model],
            input=data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=empty,
        )
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(
            f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}"
        ) from None
    if proc.returncode or out.get("is_error") or not isinstance(out.get("result"), str):
        raise RuntimeError(f"exit {proc.returncode}: {str(out.get('result'))[:300]}")
    return out["result"]


def at_night(now: datetime) -> bool:
    """Inside the window when Claude may be called without `anytime`."""
    return NIGHT[0] <= now.astimezone(load_config().tz).time() < NIGHT[1]


def ask(
    module: str,
    prompt: str,
    data: str,
    model: str = "haiku",
    timeout: int = TIMEOUT,
    anytime: bool = False,
) -> str | None:
    """Ask Claude to do `prompt` with `data` (piped in); the answer, or None if anything fails
    (no CLI, no auth, quota, timeout, daily cap). Cached: the same prompt + data + model is
    answered from earlier calls. `module` names the caller in `ai_calls`.

    Only at night (NIGHT, local time), so it doesn't use the Pro quota while the user does:
    schedule daily AI jobs in that window. Outside it, None without calling (cached answers
    still come back). `anytime=True` for what can't wait, e.g. a spoken question."""
    key, now = cache_key(prompt, data, model), utcnow()
    with Session(get_engine()) as session:
        cached = session.scalar(
            select(AiCall.result)
            .where(AiCall.key == key, AiCall.result.is_not(None))
            .order_by(AiCall.started_at.desc())
            .limit(1)
        )
        if cached is not None:
            return cached
        if not anytime and not at_night(now):
            log.warning("ai %s: not called, outside %s–%s (pass anytime=True if it can't wait)",
                        module, *NIGHT)  # fmt: skip
            return None
        today = session.scalar(
            select(func.count()).where(AiCall.started_at >= now - timedelta(days=1))
        )
    result, error, t0 = None, None, time.monotonic()
    if today >= MAX_CALLS_PER_DAY:
        error = f"daily cap reached ({MAX_CALLS_PER_DAY} calls in 24 h)"
    else:
        try:
            result = run_claude(prompt, data, model, timeout)
        except Exception as e:  # never raise: callers fall back to raw data
            error = f"{type(e).__name__}: {e}"[:500]
    seconds = time.monotonic() - t0
    log.info("ai %s: %s in %.1f s%s", module, "ok" if error is None else "failed", seconds,
             f" ({error})" if error else "")  # fmt: skip
    try:
        with Session(get_engine()) as session, session.begin():
            session.add(AiCall(module=module, key=key, started_at=now, seconds=seconds,
                               result=result, error=error))  # fmt: skip
    except Exception:  # e.g. the caller holds the write lock: the answer still counts
        log.exception("ai: could not record the call")
    return result
