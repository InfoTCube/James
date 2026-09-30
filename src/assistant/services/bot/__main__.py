"""Telegram bot: answers commands from the user's chat only.

    python -m assistant.services.bot

Needs TELEGRAM_BOT_TOKEN. Until TELEGRAM_CHAT_ID is set, /start replies with your chat id.
"""

import logging
import os

from sqlalchemy.orm import Session
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from assistant.core.config import load_config
from assistant.core.db import get_engine, utcnow
from assistant.modules.alarm import actions
from assistant.modules.alarm.models import Alarm
from assistant.services.bot.replies import (
    reply_alarm,
    reply_alarm_off,
    reply_alarms,
    reply_birthdays,
    reply_briefing,
    reply_next,
    reply_note,
    reply_note_del,
    reply_notes,
    reply_today,
    reply_weather,
)

# name → (reply function, description). One list for the handlers, /help and Telegram's menu.
COMMANDS = {
    "next": (reply_next, "Trip to your next event"),
    "today": (reply_today, "Today's events"),
    "weather": (reply_weather, "Weather and what to wear"),
    "briefing": (reply_briefing, "Morning briefing, now"),
    "alarm": (reply_alarm, "Set an alarm: /alarm 7:30 [label]"),
    "alarms": (reply_alarms, "Upcoming alarms"),
    "alarm_off": (reply_alarm_off, "Cancel an alarm: /alarm_off 3"),
    "birthdays": (reply_birthdays, "Birthdays in the next 14 days"),
    "note": (reply_note, "Save a note: /note buy HDMI cable"),
    "notes": (reply_notes, "Newest notes, or search: /notes hdmi"),
    "note_del": (reply_note_del, "Delete a note: /note_del 3"),
}


def help_text() -> str:
    lines = [f"/{name} – {desc}" for name, (_, desc) in COMMANDS.items()]
    return "\n".join([*lines, "/help – This list"])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) == os.environ.get("TELEGRAM_CHAT_ID"):
        await update.message.reply_text(help_text())
    else:
        await update.message.reply_text(
            f"Your chat id is {chat_id}. Put TELEGRAM_CHAT_ID={chat_id} in .env and restart."
        )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text())


async def register_menu(app: Application) -> None:
    """Show the commands in Telegram's "/" menu."""
    menu = [BotCommand(name, desc) for name, (_, desc) in COMMANDS.items()]
    await app.bot.set_my_commands([*menu, BotCommand("help", "List all commands")])


async def alarm_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop / Snooze pressed under an alarm message."""
    query = update.callback_query
    if str(update.effective_chat.id) != os.environ.get("TELEGRAM_CHAT_ID"):
        await query.answer()
        return
    _, what, alarm_id = query.data.split(":")
    config, now = load_config(), utcnow()
    with Session(get_engine()) as session, session.begin():
        alarm = session.get(Alarm, int(alarm_id))
        action = actions.stop if what == "stop" else actions.snooze
        done = alarm is not None and action(session, alarm, now, config)
        until = alarm.at.astimezone(config.tz) if alarm else None
    await query.answer()
    if not done:
        result = "Already handled"
    elif what == "stop":
        result = "Stopped"
    else:
        result = f"Snoozed until {until:%H:%M}"
    await query.edit_message_text(f"{query.message.text}\n{result}")  # removes the buttons


def command(reply):
    """Wrap a reply function (session, now, config, args) -> str as a Telegram handler.
    Runs in a transaction, so commands that change things (e.g. /alarm) are saved."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        with Session(get_engine()) as session, session.begin():
            text = reply(session, utcnow(), load_config(), context.args or [])
        await update.message.reply_text(text)

    return handler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request log contains the token
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logging.warning("TELEGRAM_BOT_TOKEN not set; bot not started")
        return
    app = Application.builder().token(token).post_init(register_menu).build()
    app.add_handler(CommandHandler("start", start))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        me = filters.Chat(chat_id=int(chat_id))  # everyone else is ignored
        for name, (reply, _) in COMMANDS.items():
            app.add_handler(CommandHandler(name, command(reply), filters=me))
        app.add_handler(CommandHandler("help", show_help, filters=me))
        app.add_handler(CallbackQueryHandler(alarm_button, pattern=r"^alarm:(stop|snooze):\d+$"))
    app.run_polling()


if __name__ == "__main__":
    main()
