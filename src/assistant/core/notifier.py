"""Send the user a Telegram message. The one place alerts leave the system."""

import logging
import os

import httpx
from sqlalchemy.orm import Session

from assistant.core.db import SentNotification, utcnow

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def notify(
    session: Session,
    text: str,
    key: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
) -> bool:
    """Send `text` to the user's chat. With a `key`, each key is sent at most once (recorded in
    the caller's transaction). `buttons`: (label, callback data) shown under the message; the
    bot handles the presses. Returns True if sent. Never raises: alerts are best effort."""
    if key and session.get(SentNotification, key):
        return False
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("telegram not configured, not sending: %s", text)
        return False
    try:
        body: dict = {"chat_id": chat, "text": text}
        if buttons:
            row = [{"text": label, "callback_data": data} for label, data in buttons]
            body["reply_markup"] = {"inline_keyboard": [row]}
        httpx.post(API.format(token=token), json=body, timeout=10).raise_for_status()
    except httpx.HTTPError as e:
        log.warning("telegram send failed: %s", type(e).__name__)  # no URL: it has the token
        return False
    if key:
        session.add(SentNotification(key=key, sent_at=utcnow()))
    return True
