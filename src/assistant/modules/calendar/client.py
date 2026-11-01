"""Google Calendar API over plain httpx. The only place this module talks to Google."""

import os
from datetime import date, datetime

import httpx

API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]  # read + write events


def calendar_ids() -> list[str]:
    return os.environ.get("GOOGLE_CALENDAR_IDS", "primary").split()


def _access_token() -> str:
    # ponytail: fresh token per call (a few per hour); cache it if call volume grows.
    # Move to core/ when gmail/contacts need Google too.
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=30
    )


def list_events(start: datetime, end: datetime) -> list[dict]:
    """Raw event instances (recurring ones expanded) overlapping [start, end) on all calendars."""
    items = []
    with _client() as client:
        for cal in calendar_ids():
            params = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 2500,
            }
            while True:
                resp = client.get(f"/calendars/{cal}/events", params=params)
                resp.raise_for_status()
                data = resp.json()
                items += data.get("items", [])
                if "nextPageToken" not in data:
                    break
                params["pageToken"] = data["nextPageToken"]
    return items


def add_event(
    title: str,
    start: datetime | date,
    end: datetime | date,
    location: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Create an event in Google Calendar. Returns the created event. Dates instead of datetimes
    make it all day (`end` is the day after). It shows up on the dashboard after the next
    collector run."""

    def when(t: datetime | date) -> dict:
        return {"dateTime": t.isoformat()} if isinstance(t, datetime) else {"date": t.isoformat()}

    body = {"summary": title, "start": when(start), "end": when(end)}
    if location:
        body["location"] = location
    with _client() as client:
        resp = client.post(f"/calendars/{calendar_id}/events", json=body)
        resp.raise_for_status()
        return resp.json()
