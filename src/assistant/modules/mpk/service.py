"""Place matching and departures from the imported timetable. No network here."""

import difflib
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from assistant.core.config import Config, Place, fold, has_word
from assistant.modules.calendar import service as calendar
from assistant.modules.mpk.models import (
    MpkDisruption,
    MpkEarly,
    MpkGeocode,
    MpkServiceDate,
    MpkServiceWeekday,
    MpkStop,
)

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
    then: "Connection | None" = None  # the second vehicle, if this trip has a change

    @property
    def expected_arrival(self) -> datetime:
        """Arrival with the live delay estimate added (the timetable time if there's none).
        ponytail: assumes the delay stays the same for the rest of the trip."""
        return self.arrives + timedelta(minutes=self.delay_min or 0)

    @property
    def last(self) -> "Connection":
        """The vehicle that reaches the destination."""
        return self.then or self

    @property
    def misses_transfer(self) -> bool:
        """Live delays say this vehicle reaches the change after the next one has left."""
        if self.then is None:
            return False
        leaves = self.then.departs + timedelta(minutes=self.then.delay_min or 0)
        return self.expected_arrival > leaves


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


def _rides(
    session: Session,
    services: list[str],
    earliest: int,
    latest: int,
    from_stops: list[str] | None = None,
    to_stops: list[str] | None = None,
) -> list:
    """Every ride (board at one stop, get off at a later one of the same trip) on `services`
    between the two times (seconds after midnight), limited to the given stop names."""
    o_sql, o_keys = _names_sql("o", from_stops or [])
    d_sql, d_keys = _names_sql("d", to_stops or [])
    s_sql, s_keys = _names_sql("s", services)
    from_filter = f"AND sa.name IN ({o_sql})" if from_stops else ""
    to_filter = f"AND sb.name IN ({d_sql})" if to_stops else ""
    return session.execute(
        text(f"""
            SELECT a.trip_id, a.seq AS from_seq, a.dep, b.arr, r.short_name, r.route_type,
                   t.headsign,
                   sa.name AS from_stop, sb.name AS to_stop
            FROM mpk_stop_times a
            JOIN mpk_stops sa ON sa.stop_id = a.stop_id {from_filter}
            JOIN mpk_stop_times b ON b.trip_id = a.trip_id AND b.seq > a.seq
            JOIN mpk_stops sb ON sb.stop_id = b.stop_id {to_filter}
            JOIN mpk_trips t ON t.trip_id = a.trip_id
                               AND t.service_id IN ({s_sql})
            JOIN mpk_routes r ON r.route_id = t.route_id
            WHERE a.dep >= :earliest AND b.arr <= :latest
        """),  # noqa: S608  names are bound parameters, only placeholders are formatted
        {**o_keys, **d_keys, **s_keys, "earliest": earliest, "latest": latest},
    ).all()


def _connection(r, midnight: datetime, then: Connection | None = None) -> Connection:
    return Connection(
        line=r.short_name,
        is_tram=r.route_type == 0,
        headsign=r.headsign,
        from_stop=r.from_stop,
        to_stop=r.to_stop,
        departs=midnight + timedelta(seconds=r.dep),
        arrives=midnight + timedelta(seconds=r.arr),
        trip_id=r.trip_id,
        from_seq=r.from_seq,
        then=then,
    )


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

    ponytail: same service day only (no after-midnight trips of the previous day).
    """
    day = not_before.astimezone(tz).date()
    midnight = datetime.combine(day, time.min, tz)
    services = services_on(session, day)
    if not services:
        return []
    rows = _rides(
        session,
        services,
        int((not_before - midnight).total_seconds()),
        int((arrive_by - midnight).total_seconds()),
        from_stops=origin.stops,
        to_stops=dest.stops,
    )

    best: dict[str, tuple] = {}  # per trip: board as late as possible, get off as early
    for r in rows:
        cur = best.get(r.trip_id)
        if cur is None or (r.dep, -r.arr) > (cur.dep, -cur.arr):
            best[r.trip_id] = r
    if latest_first:
        ordered = sorted(best.values(), key=lambda r: (r.arr, r.dep), reverse=True)[:limit]
    else:
        ordered = sorted(best.values(), key=lambda r: (r.dep, r.arr))[:limit]
    return [_connection(r, midnight) for r in ordered]


MIN_TRANSFER = 180  # seconds to get off, find the platform and board
TRANSFER_SAVES = timedelta(minutes=15)  # a change must beat every direct trip by this much


def transfers(
    session: Session,
    origin: Target,
    dest: Target,
    arrive_by: datetime,
    not_before: datetime,
    tz: ZoneInfo,
) -> list[Connection]:
    """Trips with one change, within the same limits as `connections`, latest departure first.
    Only the sensible ones: nothing else leaves later and arrives at least as early.

    ponytail: one change, at stops with the same name. Pl. Grunwaldzki → most Grunwaldzki on
    foot isn't considered; add a walk between nearby stops if a real trip needs it.
    """
    day = not_before.astimezone(tz).date()
    midnight = datetime.combine(day, time.min, tz)
    services = services_on(session, day)
    if not services:
        return []
    earliest = int((not_before - midnight).total_seconds())
    latest = int((arrive_by - midnight).total_seconds())
    ends = {*origin.stops, *dest.stops}  # changing here would just be a direct trip
    onward: dict[str, list] = {}
    for b in _rides(session, services, earliest, latest, to_stops=dest.stops):
        if b.from_stop not in ends:
            onward.setdefault(b.from_stop, []).append(b)
    pairs = [
        (a, b)
        for a in _rides(session, services, earliest, latest, from_stops=origin.stops)
        if a.to_stop not in ends
        for b in onward.get(a.to_stop, ())
        if b.dep - a.arr >= MIN_TRANSFER and b.short_name != a.short_name
    ]
    # latest departure first; same times: the longest wait, so the change is safest
    pairs.sort(key=lambda p: (-p[0].dep, p[1].arr, p[0].arr - p[1].dep))
    found, soonest = [], math.inf
    for a, b in pairs:
        if b.arr < soonest:
            soonest = b.arr
            found.append(_connection(a, midnight, then=_connection(b, midnight)))
    return found


def journeys(
    session: Session,
    origin: Target,
    dest: Target,
    arrive_by: datetime,
    not_before: datetime,
    tz: ZoneInfo,
    limit: int = 3,
    latest_first: bool = True,
) -> list[Connection]:
    """Like `connections`, plus trips with a change where there's no direct trip about as good
    (one that leaves and arrives within TRANSFER_SAVES of it, or better)."""
    direct = connections(session, origin, dest, arrive_by, not_before, tz, limit, latest_first)
    changes = [
        t
        for t in transfers(session, origin, dest, arrive_by, not_before, tz)
        if not any(
            d.departs >= t.departs - TRANSFER_SAVES and d.arrives <= t.last.arrives + TRANSFER_SAVES
            for d in direct
        )
    ]
    if latest_first:
        key = lambda c: (-c.last.arrives.timestamp(), -c.departs.timestamp())  # noqa: E731
    else:
        key = lambda c: (c.departs, c.last.arrives)  # noqa: E731
    return sorted([*direct, *changes], key=key)[:limit]


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
    disruptions: list[str] = field(default_factory=list)  # MPK changes on its lines, formatted


def connection_lines(options: list[Connection]) -> set[str]:
    """Every line in these connections, the one after a change included."""
    lines = set()
    for c in options:
        while c:
            lines.add(c.line)
            c = c.then
    return lines


def disruptions_for(session: Session, lines: set[str], day: date) -> list[str]:
    """MPK's planned changes on `day` touching any of `lines`, e.g.
    "⚠️ 144: remont torowiska na ul. Łokietka (until 30.11) https://mpk.wroc.pl/…"."""
    if not lines:
        return []
    q = select(MpkDisruption).where(MpkDisruption.valid_from <= day, MpkDisruption.valid_to >= day)
    found = []
    for d in session.scalars(q.order_by(MpkDisruption.valid_to)):
        hit = [ln for ln in d.lines.split() if ln in lines]
        if hit:
            title = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s*r?\.?\s*-\s*", "", d.title)
            found.append(f"⚠️ {', '.join(hit)}: {title} (until {d.valid_to:%d.%m}) {d.url}")
    return found


def _home(config: Config) -> Target:
    return Target("home", config.places["home"].stops, config.places["home"].walk_minutes)


MAX_EARLY = 120  # minutes


def early_rules(session: Session) -> list[MpkEarly]:
    """The "arrive early" rules, by title."""
    return list(session.scalars(select(MpkEarly).order_by(MpkEarly.title)))


def early_minutes(rules: list[MpkEarly], title: str) -> int:
    """How many minutes early to arrive for an event called `title` (the biggest matching rule;
    a rule matches titles containing its words: "Siatkówka" matches "Siatkówka 🏐")."""
    return max((r.minutes for r in rules if has_word(title, r.title)), default=0)


def set_early(session: Session, title: str, minutes: int) -> str:
    """Arrive `minutes` early (0 removes the rule, at most MAX_EARLY) for events called
    `title`. Emoji etc. are dropped from the title: the name as stored, e.g. "Siatkówka"."""
    title = "".join(c for c in title if c.isalnum() or c in " .#+-&'").strip()
    rule = session.get(MpkEarly, fold(title))
    if minutes <= 0:
        if rule:
            session.delete(rule)
    elif rule:
        rule.minutes = min(minutes, MAX_EARLY)
    else:
        session.add(MpkEarly(key=fold(title), title=title, minutes=min(minutes, MAX_EARLY)))
    return title


def legs_between(session: Session, start: datetime, end: datetime, config: Config) -> list[Leg]:
    """Trips planned for the located events in [start, end). An event with an "arrive early"
    rule is planned as starting that much earlier (leave-by, alerts and the alarm follow)."""
    events = calendar.events_between(session, start, end, config.tz)
    rules = early_rules(session)
    targets: dict[str, Target | None] = {}
    located = []
    for e in events:
        if e.location and not e.all_day:
            if e.location not in targets:
                targets[e.location] = find_target(session, e.location, config.places)
            early = timedelta(minutes=early_minutes(rules, e.title))
            located.append((e.title, e.start - early, e.end, targets[e.location]))
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
        options = journeys(
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
            options = journeys(
                session,
                leg.origin,
                leg.dest,
                arrive_by=arrive_by + LATE_WINDOW,
                not_before=not_before,
                tz=config.tz,
                latest_first=False,
            )
            options.sort(key=lambda c: c.last.arrives)
            if options:
                walk = timedelta(minutes=leg.dest.walk_minutes)
                late = options[0].last.arrives + walk - leg.arrive_by
                late_min = max(1, round(late.total_seconds() / 60))
        if options and leg.arrive_by:
            leave_by = options[0].departs - timedelta(minutes=leg.origin.walk_minutes)
    else:
        late_min = None
    end = timetable_end(session)
    expired = end if end and end < now.astimezone(config.tz).date() else None
    day = now.astimezone(config.tz).date()
    changes = disruptions_for(session, connection_lines(options), day)
    return Trip(leg, options, leave_by, expired, late_min, changes)


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
        options = journeys(
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
