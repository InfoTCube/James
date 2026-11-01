"""Reply texts for bot commands. Plain functions over the DB, no Telegram here."""

from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from assistant.core.config import Config
from assistant.modules.alarm import service as alarm
from assistant.modules.birthdays import service as birthdays
from assistant.modules.calendar import service as calendar
from assistant.modules.events import service as events
from assistant.modules.flights import service as flights
from assistant.modules.jobs import service as jobs
from assistant.modules.mpk import service as mpk
from assistant.modules.mpk.alerts import format_connection
from assistant.modules.news import service as news
from assistant.modules.notes import service as notes
from assistant.modules.weather import service as weather


def reply_next(session: Session, now: datetime, config: Config, args: list[str] = ()) -> str:
    """/next: the trip you need now, else your next event with a location."""
    tz = config.tz
    trip = mpk.current_trip(session, now, config)
    if trip:
        leg = trip.leg
        where = leg.title if leg.arrive_by else "home"
        head = f"{leg.origin.label if leg.origin else '?'} → {where}"
        if leg.arrive_by:
            head += f" ({leg.arrive_by.astimezone(tz):%H:%M})"
        lines = [head]
        if trip.timetable_end:
            lines.append(f"⚠️ Timetable ended {trip.timetable_end:%d.%m}: last week's times")
        if trip.late_min:
            lines.append(f"You'll be ~{trip.late_min} min late.")
        if trip.leave_by:
            lines.append(f"Leave by {trip.leave_by.astimezone(tz):%H:%M}")
        lines += [format_connection(c, tz) for c in trip.options] or ["No direct connection found"]
        return "\n".join(lines)

    event = calendar.next_event_with_location(session, now, tz)
    if event is None:
        return "Nothing planned with a location in the next 7 days."
    shows = event.start - mpk.LOOKAHEAD
    return (
        f"Next: {event.title}, {event.start.astimezone(tz):%a %d.%m %H:%M} at {event.location}.\n"
        f"Connections show up from {shows.astimezone(tz):%H:%M}."
    )


EARLY_USAGE = "Usage: /early Siatkówka 15 (minutes; 0 removes it), /early 15 for your next event"


def reply_early(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/early Siatkówka 15: arrive 15 min early for events called Siatkówka (0 removes it);
    /early 15: for your next event; /early: the rules."""
    if not args:
        rules = mpk.early_rules(session)
        if not rules:
            return f"Arriving on time for everything.\n{EARLY_USAGE}"
        return "\n".join(f"{r.title}: {r.minutes} min early" for r in rules)
    if not args[-1].isdigit():
        return EARLY_USAGE
    minutes, title = min(int(args[-1]), mpk.MAX_EARLY), " ".join(args[:-1])
    if not title:
        event = calendar.next_event_with_location(session, now, config.tz)
        if event is None:
            return "No upcoming event with a location. Name it: /early Siatkówka 15"
        title = event.title
    title = mpk.set_early(session, title, minutes)
    if not minutes:
        return f"{title}: arriving on time again."
    return f"⏪ Leaving {minutes} min earlier for {title} from now on."


def reply_today(session: Session, now: datetime, config: Config, args: list[str] = ()) -> str:
    """/today: today's events."""
    return reply_day(session, now.astimezone(config.tz).date(), config, "today")


def reply_day(session: Session, day: date, config: Config, label: str) -> str:
    """Events on `day` ("Nothing in the calendar {label}." if none)."""
    tz = config.tz
    events = calendar.day_events(session, datetime.combine(day, time(12), tz), tz)
    if not events:
        return f"Nothing in the calendar {label}."
    lines = []
    for e in events:
        when = (
            "all day"
            if e.all_day
            else f"{e.start.astimezone(tz):%H:%M}–{e.end.astimezone(tz):%H:%M}"
        )
        lines.append(f"{when}  {e.title}" + (f" · {e.location}" if e.location else ""))
    return "\n".join(lines)


def reply_weather(session: Session, now: datetime, config: Config, args: list[str] = ()) -> str:
    """/weather: now + what to wear for today's time outside."""
    tz = config.tz
    hours = weather.get_hours(session, now - timedelta(hours=1), now + timedelta(hours=1))
    if not hours:
        return "No forecast yet."
    h = hours[0]
    label, icon = weather.describe(h.code)
    lines = [f"{icon} {h.temp:.0f}° {label}, feels {h.feels_like:.0f}°"]
    advice = weather.get_clothing_advice(
        session, now, tz, calendar.outdoor_events(session, now, tz)
    )
    if advice:
        lines.append(f"👕 {advice.layers}")
        lines += advice.extras
    return "\n".join(lines)


def reply_alarm(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/alarm 7:30 [label]: set an alarm for the next 7:30."""
    at = alarm.parse_time(args[0], now, config.tz) if args else None
    if at is None:
        return "Usage: /alarm 7:30 [label]"
    a = alarm.add_alarm(session, at, " ".join(args[1:]) or None)
    return f"⏰ Alarm #{a.id} set for {at.astimezone(config.tz):%a %d.%m %H:%M}"


def reply_alarms(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/alarms: upcoming alarms."""
    alarms = alarm.upcoming_alarms(session, now)
    if not alarms:
        return "No alarms set."
    return "\n".join(
        f"#{a.id} {a.at.astimezone(config.tz):%a %d.%m %H:%M}"
        + (f" · {a.label}" if a.label else "")
        + (" (auto)" if a.source == "auto" else "")
        for a in alarms
    )


def reply_alarm_off(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/alarm_off 3: cancel alarm #3."""
    if not args or not args[0].lstrip("#").isdigit():
        return "Usage: /alarm_off 3 (numbers from /alarms)"
    a = alarm.cancel_alarm(session, int(args[0].lstrip("#")))
    return f"Alarm #{a.id} cancelled." if a else "No such upcoming alarm."


def reply_briefing(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/briefing: the morning briefing, now."""
    return alarm.briefing(session, now, config)


def reply_birthdays(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/birthdays: the next 14 days."""
    items = birthdays.upcoming(session, now, config.tz, days=14)
    if not items:
        return "No birthdays in the next 14 days."
    today = now.astimezone(config.tz).date()
    return "\n".join(f"🎂 {b.name} – {birthdays.when(b.day, today)}" for b in items)


def _note_line(n, tz) -> str:
    return f"#{n.id} {n.created_at.astimezone(tz):%d.%m} {n.text}"


def reply_note(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/note buy HDMI cable: save a note."""
    if not args:
        return "Usage: /note buy HDMI cable"
    n = notes.add_note(session, " ".join(args), now)
    return f"📝 Saved #{n.id}"


def reply_notes(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/notes [words]: newest notes, or those containing all the words."""
    found = notes.search_notes(session, " ".join(args))
    if not found:
        return "No matching notes." if args else "No notes yet. Add one: /note buy HDMI cable"
    return "\n".join(_note_line(n, config.tz) for n in found)


def reply_note_del(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/note_del 3: delete note #3."""
    if not args or not args[0].lstrip("#").isdigit():
        return "Usage: /note_del 3 (numbers from /notes)"
    n = notes.delete_note(session, int(args[0].lstrip("#")))
    return f"🗑️ Deleted #{n.id}: {n.text}" if n else "No such note."


def reply_jobs(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/jobs: matching offers from the last 7 days, best fit (⭐) first."""
    cfg = config.jobs
    if not cfg.keywords:
        return "Job alerts are off: set jobs.keywords in config.yaml."
    offers = jobs.recent(session, now, cfg)
    if not offers:
        return "No matching job offers in the last 7 days."
    shown = [
        f"{'⭐ ' * jobs.best_fit(o, cfg)}{o.title} – {o.company}\n"
        + " · ".join(filter(None, [jobs.work_mode(o), jobs.hours(o)]))
        + f"\n{o.url}"
        for o in offers[:20]
    ]
    if len(offers) > 20:
        shown.append(f"…and {len(offers) - 20} more")
    return "\n\n".join(shown)


def reply_events(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/events: matching events in the next 30 days, soonest first."""
    upcoming = events.upcoming(session, now.astimezone(config.tz).date())
    if not upcoming:
        return "No matching events in the next 30 days."
    shown = [events.format_event(e, config.tz) for e in upcoming[:20]]
    if len(upcoming) > 20:
        shown.append(f"…and {len(upcoming) - 20} more")
    return "\n\n".join(shown)


def reply_news(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/news: today's digest (made overnight) and the latest headlines with links; just the
    headlines without a digest."""
    if not config.news.feeds:
        return "News is off: add feeds under news.feeds in config.yaml."
    parts = []
    d = news.digest(session, now.astimezone(config.tz).date())
    if d:
        parts.append("📰 Today's digest\n" + "\n".join(f"• {p}" for p in news.bullets(d)))
    latest = news.latest(session, now)
    if latest:
        parts.append("Latest:" if d else "📰 Latest headlines (no digest yet):")
        parts += [f"{i.title} ({i.feed})\n{i.link}" for i in latest]
    return "\n\n".join(parts) or "No news yet: the first fetch is within the hour."


def reply_flight_add(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/flight_add any 1.11-30.11 2-4 300: watch for flights from WRO under a price."""
    try:
        w = flights.parse_watch(args, now.astimezone(config.tz).date())
    except ValueError as e:
        return f"{e}\n\n{flights.USAGE}"
    session.add(w)
    session.flush()  # assigns the id
    return f"✈️ Watching {flights.describe(w)}\nFirst check within 15 minutes."


def reply_flights(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/flights: watches with the 3 cheapest trips found for each (✅ = under the price)."""
    watches = flights.watches(session)
    if not watches:
        return f"No flight watches.\n\n{flights.USAGE}"
    parts = []
    for w in watches:
        lines = [flights.describe(w)]
        if w.checked_at is None:
            lines.append("not checked yet")
        for f in flights.fares(session, w.id)[:3]:
            lines.append(f"{'✅' if f.price <= w.max_price else '·'} {flights.format_fare(f)}")
        if w.checked_at and len(lines) == 1:
            lines.append("no flights found")
        parts.append("\n".join(lines))
    parts.append("Change a price: /flight_price 3 250 · delete: /flight_del 3")
    return "\n\n".join(parts)


def reply_flight_price(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/flight_price 3 250: new max price for watch #3."""
    usage = "Usage: /flight_price 3 250 (watch number from /flights, price in PLN)"
    if len(args) != 2 or not args[0].lstrip("#").isdigit():
        return usage
    w = flights.get_watch(session, int(args[0].lstrip("#")))
    if w is None:
        return "No such watch."
    try:
        w.max_price = flights.parse_price(args[1])
    except ValueError:
        return usage
    w.checked_at = None  # check again soon: deals under the new price get sent
    return f"✈️ Updated {flights.describe(w)}"


def reply_flight_del(session: Session, now: datetime, config: Config, args: list[str]) -> str:
    """/flight_del 3: stop watching #3."""
    if not args or not args[0].lstrip("#").isdigit():
        return "Usage: /flight_del 3 (numbers from /flights)"
    w = flights.delete_watch(session, int(args[0].lstrip("#")))
    return f"🗑️ Deleted {flights.describe(w)}" if w else "No such watch."
