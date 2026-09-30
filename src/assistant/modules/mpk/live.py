"""Live delay estimates for connections: positions from client, matching logic from service."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from assistant.modules.mpk import client, service


def add_live_delays(
    session: Session, options: list[service.Connection], now: datetime, tracker: service.Tracker
) -> None:
    """Fill in delay_min from live vehicle positions (best effort, never raises).
    Each caller keeps its own tracker and must call this regularly (e.g. every minute):
    direction of travel needs two readings."""
    trams = {c.line for c in options if c.is_tram}
    buses = {c.line for c in options if not c.is_tram}
    vehicles = service.parse_positions(client.vehicle_positions(trams, buses))
    tracker.update(vehicles, now)
    for c in options:
        stops = service.trip_stops_until(session, c.trip_id, c.from_seq)
        if stops:
            midnight = c.departs - timedelta(seconds=stops[-1][2])
            line_vehicles = [v for v in vehicles if v.line == c.line]
            c.delay_min = service.estimate_delay(stops, midnight, line_vehicles, tracker, now)
