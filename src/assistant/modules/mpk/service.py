"""Place matching and departures from the imported timetable. No network here."""

import difflib
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from assistant.core.config import Config, Place, fold
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk.models import MpkGeocode, MpkServiceDate, MpkServiceWeekday, MpkStop

DEFAULT_WALK = 5  # minutes, for stops matched by name rather than a configured place
STOP_RADIUS = 400  # m: stops considered near a looked-up address
PLACE_RADIUS = 300  # m: an address this close to one of a place's stops counts as that place
WALK_SPEED = 80  # m per minute

LOOKAHEAD = timedelta(hours=2)  # show a trip to an event from this long before it starts
END_LEAD = timedelta(minutes=30)  # show a trip from an event from this long before it ends
HOME_GRACE = timedelta(hours=1)  # ...and a trip home until this long after it ends
CHAIN_GAP = timedelta(hours=1, minutes=30)  # next event this soon after: go straight there


@dataclass
class Target:
    label: str  # place alias or stop name
    stops: list[str]  # stop names
    walk_minutes: int


@dataclass
class Connection:
    line: str
    is_tram: bool
    headsign: str
    from_stop: str
    to_stop: str
    departs: datetime
    arrives: datetime
    trip_id: str = ""
    from_seq: int = 0
    delay_min: int | None = None  # live estimate; None = unknown

    @property
    def expected_arrival(self) -> datetime:
        """Arrival with the live delay estimate added (the timetable time if there's none).
        ponytail: assumes the delay stays the same for the rest of the trip."""
        return self.arrives + timedelta(minutes=self.delay_min or 0)


def resolve(location: str, places: dict[str, Place], stop_names: list[str]) -> Target | None:
    """Map free text (calendar location, later chat/voice) to stops.

    1. a place alias or one of its keywords appears in the text ("uni", "Politechnika, C-13")
    2. a stop name appears in the text ("Pl. Grunwaldzki 1" → PL. GRUNWALDZKI)
    3. fuzzy match against all stop names ("Grunwaldzky" → PL. GRUNWALDZKI)
    A matched stop that belongs to a place resolves to that place.
    """
    loc = re.sub(r"\bplac\b", "pl.", fold(location))  # timetable writes "PL. GRUNWALDZKI"
    words = set(re.findall(r"\w+", loc))
    for alias, place in places.items():
        if alias in words or any(fold(k) in loc for k in place.keywords):
            return Target(alias, place.stops, place.walk_minutes)

    by_fold: dict[str, list[str]] = {}  # "dworzec glowny" → tram + bus spellings
    for name in stop_names:
        by_fold.setdefault(fold(name), []).append(name)
    inside = [f for f in by_fold if len(f) > 3 and re.search(rf"\b{re.escape(f)}\b", loc)]
    close = difflib.get_close_matches(loc, list(by_fold), n=1, cutoff=0.8)
    match = max(inside, key=len) if inside else (close[0] if close else None)
    if match is None:
        return None
    names = by_fold[match]
    for alias, place in places.items():
        if set(names) & set(place.stops):
            return Target(alias, place.stops, place.walk_minutes)
    return Target(names[0], names, DEFAULT_WALK)


def stop_names(session: Session) -> list[str]:
    return list(session.scalars(select(MpkStop.name).distinct()))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(a))


def resolve_near(
    lat: float, lon: float, label: str, places: dict[str, Place], stops: list[MpkStop]
) -> Target | None:
    """Stops near a point: one of your places if it's that close, else the nearby stops."""
    near: dict[str, float] = {}
    for s in stops:
        d = distance_m(lat, lon, s.lat, s.lon)
        if d <= STOP_RADIUS and d < near.get(s.name, math.inf):
            near[s.name] = d
    if not near:
        return None
    for alias, place in places.items():
        if any(near.get(n, math.inf) <= PLACE_RADIUS for n in place.stops):
            return Target(alias, place.stops, place.walk_minutes)
    names = sorted(near, key=near.__getitem__)
    return Target(label, names, max(1, math.ceil(near[names[0]] / WALK_SPEED)))


def find_target(session: Session, location: str, places: dict[str, Place]) -> Target | None:
    """Text rules first (`resolve`), then the cached address lookup made by the collector."""
    if target := resolve(location, places, stop_names(session)):
        return target
    geo = session.get(MpkGeocode, location)
    if geo and geo.lat is not None:
        stops = list(session.scalars(select(MpkStop)))
        return resolve_near(geo.lat, geo.lon, location.split(",")[0], places, stops)
    return None


@dataclass
class Leg:
    """One trip of the day. Shown on the dashboard while show_from <= now < show_until."""

    title: str  # where you're going: event title or "Home"
    origin: Target | None  # None: the previous event's location didn't match any stop
    dest: Target | None  # None: the event's location didn't match any stop
    not_before: datetime | None  # can't leave before this (previous event's end); None = now
    arrive_by: datetime | None  # event start; None = going home, no deadline
    show_from: datetime
    show_until: datetime


def plan_legs(
    events: list[tuple[str, datetime, datetime, Target | None]], home: Target
) -> list[Leg]:
    """Trips for located events (title, start, end, target), sorted by start.

    Home → first event. Between events: straight on if the next starts within CHAIN_GAP of
    this one ending, otherwise home and out again. Last event → home. No trip between two
    events at the same place.
    """
    legs: list[Leg] = []

    def go_home(end: datetime, origin: Target | None) -> None:
        if origin is None or origin.label != home.label:
            legs.append(Leg("Home", origin, home, end, None, end - END_LEAD, end + HOME_GRACE))

    prev = None
    for title, start, end, target in events:
        if prev and start - prev[2] <= CHAIN_GAP:
            origin, not_before, show_from = prev[3], prev[2], prev[2] - END_LEAD
        else:
            if prev:
                go_home(prev[2], prev[3])
            origin, not_before, show_from = home, None, start - LOOKAHEAD
        same_place = target and origin and origin.label == target.label
        if not same_place:
            legs.append(Leg(title, origin, target, not_before, start, show_from, start))
        prev = (title, start, end, target)
    if prev:
        go_home(prev[2], prev[3])
    return legs


def timetable_end(session: Session) -> date | None:
    """Last day the imported timetable covers."""
    return session.scalar(select(func.max(MpkServiceDate.day)))


def services_on(session: Session, day: date) -> list[str]:
    """Service ids running on `day`. After the timetable ends: the services that normally
    run on that weekday (holiday exceptions ignored), until a new timetable arrives."""
    end = timetable_end(session)
    if end is not None and day > end:
        q = select(MpkServiceWeekday.service_id).where(MpkServiceWeekday.weekday == day.weekday())
    else:
        q = select(MpkServiceDate.service_id).where(MpkServiceDate.day == day)
    return list(session.scalars(q))


def _names_sql(prefix: str, names: list[str]) -> tuple[str, dict]:
    keys = {f"{prefix}{i}": n for i, n in enumerate(names)}
    return ", ".join(f":{k}" for k in keys), keys


def connections(
    session: Session,
    origin: Target,
    dest: Target,
    arrive_by: datetime,
    not_before: datetime,
    tz: ZoneInfo,
    limit: int = 3,
    latest_first: bool = True,
) -> list[Connection]:
    """Direct trips from origin to dest that leave the origin stop at/after `not_before`
    and reach the dest stop by `arrive_by`, one row per trip. latest_first: latest arrivals
    first (you have a deadline); otherwise earliest departures first (going home).

    ponytail: direct trips only, same service day only (no after-midnight trips of the
    previous day). Add transfers (e.g. RAPTOR) if a common trip needs a change.
    """
    day = not_before.astimezone(tz).date()
    midnight = datetime.combine(day, time.min, tz)
    services = services_on(session, day)
    if not services:
        return []
    o_sql, o_keys = _names_sql("o", origin.stops)
    d_sql, d_keys = _names_sql("d", dest.stops)
    s_sql, s_keys = _names_sql("s", services)
    rows = session.execute(
        text(f"""
            SELECT a.trip_id, a.seq AS from_seq, a.dep, b.arr, r.short_name, r.route_type,
                   t.headsign,
                   sa.name AS from_stop, sb.name AS to_stop
            FROM mpk_stop_times a
            JOIN mpk_stops sa ON sa.stop_id = a.stop_id AND sa.name IN ({o_sql})
            JOIN mpk_stop_times b ON b.trip_id = a.trip_id AND b.seq > a.seq
            JOIN mpk_stops sb ON sb.stop_id = b.stop_id AND sb.name IN ({d_sql})
            JOIN mpk_trips t ON t.trip_id = a.trip_id
                               AND t.service_id IN ({s_sql})
            JOIN mpk_routes r ON r.route_id = t.route_id
            WHERE a.dep >= :earliest AND b.arr <= :latest
        """),  # noqa: S608  names are bound parameters, only placeholders are formatted
        {
            **o_keys,
            **d_keys,
            **s_keys,
            "earliest": int((not_before - midnight).total_seconds()),
            "latest": int((arrive_by - midnight).total_seconds()),
        },
    ).all()

    best: dict[str, tuple] = {}  # per trip: board as late as possible, get off as early
    for r in rows:
        cur = best.get(r.trip_id)
        if cur is None or (r.dep, -r.arr) > (cur.dep, -cur.arr):
            best[r.trip_id] = r
    if latest_first:
        ordered = sorted(best.values(), key=lambda r: (r.arr, r.dep), reverse=True)[:limit]
    else:
        ordered = sorted(best.values(), key=lambda r: (r.dep, r.arr))[:limit]
    return [
        Connection(
            line=r.short_name,
            is_tram=r.route_type == 0,
            headsign=r.headsign,
            from_stop=r.from_stop,
            to_stop=r.to_stop,
            departs=midnight + timedelta(seconds=r.dep),
            arrives=midnight + timedelta(seconds=r.arr),
            trip_id=r.trip_id,
            from_seq=r.from_seq,
        )
        for r in ordered
    ]


# --- live delays -------------------------------------------------------------------------------
# There's no official real-time delay feed for Wrocław, only live positions. A vehicle of the
# right line that's near a stop *before* your boarding stop, and moving towards it, is assumed to
# be your trip; delay = now - scheduled time at that stop.

MATCH_RADIUS = 150  # m: vehicle this close to a stop is "at" it
MOVED = 40  # m: smaller position changes are GPS noise / standing at a stop
EARLIEST, LATEST = -2, 20  # minutes: plausible delays; outside that it's another trip
FORGET = timedelta(minutes=10)


@dataclass
class Vehicle:
    course: int  # MPK's id for the run ("k"); not a GTFS trip id
    line: str
    lat: float
    lon: float


def parse_positions(data: list[dict]) -> list[Vehicle]:
    """mpk.wroc.pl/bus_position JSON → vehicles. Note: its x is latitude, y is longitude."""
    return [Vehicle(int(v["k"]), str(v["name"]), float(v["x"]), float(v["y"])) for v in data]


class Tracker:
    """Remembers where each vehicle was, to tell which way it's moving.

    ponytail: lives in the API process and only learns while the card polls (once a minute),
    so the first estimate appears on the second refresh. Move polling to the worker if the
    Telegram "leave now" alert needs it without the dashboard open.
    """

    def __init__(self) -> None:
        self._pos: dict[int, tuple[float, float, datetime]] = {}  # last significant position
        self._anchor: dict[int, tuple[float, float]] = {}  # the one before it

    def update(self, vehicles: list[Vehicle], now: datetime) -> None:
        for v in vehicles:
            last = self._pos.get(v.course)
            if last is None or now - last[2] > FORGET:
                self._pos[v.course] = (v.lat, v.lon, now)
                self._anchor.pop(v.course, None)
            elif distance_m(last[0], last[1], v.lat, v.lon) >= MOVED:
                self._anchor[v.course] = (last[0], last[1])
                self._pos[v.course] = (v.lat, v.lon, now)
        for course in [c for c, p in self._pos.items() if now - p[2] > FORGET]:
            self._pos.pop(course)
            self._anchor.pop(course, None)

    def approaching(self, v: Vehicle, lat: float, lon: float) -> bool:
        """True if the vehicle's last real move brought it closer to (lat, lon)."""
        anchor = self._anchor.get(v.course)
        if anchor is None:
            return False
        return distance_m(v.lat, v.lon, lat, lon) < distance_m(anchor[0], anchor[1], lat, lon)


def trip_stops_until(session: Session, trip_id: str, seq: int) -> list[tuple[float, float, int]]:
    """(lat, lon, scheduled departure seconds) of the trip's stops up to and including seq."""
    q = text("""
        SELECT s.lat, s.lon, st.dep FROM mpk_stop_times st JOIN mpk_stops s USING (stop_id)
        WHERE st.trip_id = :trip AND st.seq <= :seq ORDER BY st.seq
    """)
    return [tuple(r) for r in session.execute(q, {"trip": trip_id, "seq": seq})]


def _where_on_trip(v: Vehicle, stops: list[tuple[float, float, int]]) -> tuple[float, float]:
    """(distance to the trip in m, scheduled seconds for that point). Between two stops the
    schedule is interpolated along the straight line joining them."""
    ky, kx = 110_540, 111_320 * math.cos(math.radians(v.lat))  # metres per degree, locally flat
    best = (distance_m(v.lat, v.lon, stops[0][0], stops[0][1]), float(stops[0][2]))
    for (lat1, lon1, t1), (lat2, lon2, t2) in pairwise(stops):
        ax, ay = (lon2 - lon1) * kx, (lat2 - lat1) * ky
        px, py = (v.lon - lon1) * kx, (v.lat - lat1) * ky
        f = min(1.0, max(0.0, (px * ax + py * ay) / ((ax * ax + ay * ay) or 1)))
        d = math.hypot(px - f * ax, py - f * ay)
        if d < best[0]:
            best = (d, t1 + f * (t2 - t1))
    return best


def estimate_delay(
    stops: list[tuple[float, float, int]],
    midnight: datetime,
    vehicles: list[Vehicle],
    tracker: Tracker,
    now: datetime,
) -> int | None:
    """Minutes late (negative = early) of the trip whose stops up to your boarding stop are
    `stops`, judged from live vehicles of its line. None if no vehicle fits."""
    if not stops:
        return None
    board = stops[-1]
    best = None
    for v in vehicles:
        d, scheduled = _where_on_trip(v, stops)
        if d > MATCH_RADIUS or not tracker.approaching(v, board[0], board[1]):
            continue
        delay = (now - (midnight + timedelta(seconds=scheduled))).total_seconds() / 60
        if EARLIEST <= delay <= LATEST and (best is None or abs(delay) < abs(best)):
            best = delay
    return None if best is None else round(best)


# --- the trip you need right now -----------------------------------------------------------------

HOME_WINDOW = timedelta(hours=2)  # how far ahead to look for a ride home
LATE_WINDOW = timedelta(minutes=20)  # nothing on time: show connections arriving this late


@dataclass
class Trip:
    leg: Leg
    options: list[Connection]
    leave_by: datetime | None  # for trips with a deadline: last moment to leave the origin
    timetable_end: date | None  # set when the timetable has run out (times are a fallback)
    late_min: int | None = None  # nothing on time: minutes late with the first option


def _home(config: Config) -> Target:
    return Target("home", config.places["home"].stops, config.places["home"].walk_minutes)


def legs_between(session: Session, start: datetime, end: datetime, config: Config) -> list[Leg]:
    """Trips planned for the located events in [start, end)."""
    events = calendar.events_between(session, start, end, config.tz)
    targets: dict[str, Target | None] = {}
    located = []
    for e in events:
        if e.location and not e.all_day:
            if e.location not in targets:
                targets[e.location] = find_target(session, e.location, config.places)
            located.append((e.title, e.start, e.end, targets[e.location]))
    return plan_legs(located, _home(config))


def day_legs(session: Session, now: datetime, config: Config) -> list[Leg]:
    """All trips planned around now (events from yesterday to tomorrow)."""
    return legs_between(session, now - timedelta(days=1), now + timedelta(days=1), config)


def current_trip(session: Session, now: datetime, config: Config) -> Trip | None:
    """The trip to show right now (dashboard card, bot, alerts), or None."""
    if "home" not in config.places:
        return None
    leg = next(
        (g for g in day_legs(session, now, config) if g.show_from <= now < g.show_until), None
    )
    if leg is None:
        return None
    options, leave_by = [], None
    if leg.origin and leg.dest:
        not_before = max(now, leg.not_before or now) + timedelta(minutes=leg.origin.walk_minutes)
        if leg.arrive_by:
            arrive_by = leg.arrive_by - timedelta(minutes=leg.dest.walk_minutes)
        else:
            arrive_by = not_before + HOME_WINDOW
        options = connections(
            session,
            leg.origin,
            leg.dest,
            arrive_by=arrive_by,
            not_before=not_before,
            tz=config.tz,
            latest_first=leg.arrive_by is not None,
        )
        late_min = None
        if not options and leg.arrive_by:  # you'll be late: earliest arrivals, up to 20 min late
            options = connections(
                session,
                leg.origin,
                leg.dest,
                arrive_by=arrive_by + LATE_WINDOW,
                not_before=not_before,
                tz=config.tz,
                latest_first=False,
            )
            options.sort(key=lambda c: c.arrives)
            if options:
                walk = timedelta(minutes=leg.dest.walk_minutes)
                late = options[0].arrives + walk - leg.arrive_by
                late_min = max(1, round(late.total_seconds() / 60))
        if options and leg.arrive_by:
            leave_by = options[0].departs - timedelta(minutes=leg.origin.walk_minutes)
    else:
        late_min = None
    end = timetable_end(session)
    expired = end if end and end < now.astimezone(config.tz).date() else None
    return Trip(leg, options, leave_by, expired, late_min)


FALLBACK_TRAVEL = timedelta(minutes=45)  # no connection found: assume this much door to door


@dataclass
class Departure:
    leg: Leg
    leave_by: datetime
    connection: Connection | None  # None: leave_by is the FALLBACK_TRAVEL guess


def first_departure(
    session: Session, day: date, config: Config, after: datetime | None = None
) -> Departure | None:
    """When you first have to leave home on `day` (for the alarm), or with `after`, the first
    time from home for an event starting after it (morning briefing). None if there's none."""
    if "home" not in config.places:
        return None
    start = datetime.combine(day, time.min, config.tz)
    legs = legs_between(session, start, start + timedelta(days=1), config)
    leg = next(
        (
            g
            for g in legs
            if g.arrive_by
            and g.origin
            and g.origin.label == "home"
            and (after is None or g.arrive_by > after)
        ),
        None,
    )
    if leg is None:
        return None
    if leg.dest:
        options = connections(
            session,
            leg.origin,
            leg.dest,
            arrive_by=leg.arrive_by - timedelta(minutes=leg.dest.walk_minutes),
            not_before=leg.arrive_by - LOOKAHEAD,
            tz=config.tz,
        )
        if options:
            walk = timedelta(minutes=leg.origin.walk_minutes)
            return Departure(leg, options[0].departs - walk, options[0])
    return Departure(leg, leg.arrive_by - FALLBACK_TRAVEL, None)
