# calendar

- **Source:** Google Calendar API (free for personal use), scope `calendar.events` (read + write).
- **Schedule:** every 15 min. Fetches today + 14 days with recurring events expanded, then replaces
  `calendar_events` completely, which picks up deleted and moved events. A failed fetch leaves the
  old data untouched. Cancelled events and events you declined are skipped.
- **Config (`.env`):** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, and
  `GOOGLE_CALENDAR_IDS`, which is optional. It defaults to `primary`; separate extra calendar IDs
  with spaces.
- **Service:** `day_events`, `events_between`, `outdoor_events` (today's timed events with a
  location, fed to weather's clothing advice), `next_event_with_location` (for mpk).
- **Write:** `client.add_event(title, start, end, location=None)` creates an event, for the bot and
  voice later.
- **Widget:** `GET /widgets/calendar` shows the rest of today and tomorrow. Marked stale after 1 h
  without a successful run.

## One-time Google setup

1. Go to https://console.cloud.google.com, create a project and enable the **Google Calendar API**.
2. Open **Google Auth Platform**. Set the app up as *External*, add your Gmail as a test user, then
   go to **Audience** and click **Publish app** (set it to *In production*). **Don't skip
   publishing.** In *Testing* mode, refresh tokens expire after 7 days. The app doesn't need
   verification for your own account. Google will warn that the app is unverified: click
   *Advanced → Go to …*.
3. Open **Clients**, create one with type *Desktop app*, and put its ID and secret in `.env`.
4. Run `uv run --env-file .env python -m assistant.modules.calendar.auth`, sign in in the browser.
   The script saves `GOOGLE_REFRESH_TOKEN` into `.env` itself.

**Fragility:** low. If the token is revoked or expires, the collector fails with a 400/401 and the
widget goes stale. Fix it by re-running step 4.
