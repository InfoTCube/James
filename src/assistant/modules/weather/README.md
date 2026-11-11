# weather

- **Source:** [Open-Meteo](https://open-meteo.com/en/docs) forecast API. Free, no key.
- **Schedule:** hourly. Fetches 2 days of hourly data and upserts into `weather_hourly` (UTC).
- **Config:** `location.latitude`, `location.longitude`.
- **Service:** `get_clothing_advice(session, now, tz, events=None)`. The widget passes
  `calendar.service.outdoor_events()`. The advice covers only the time you're likely outside.
  You're inside during events. You're outside 30 min before the first event, 30 min after the
  last, and during any break longer than 1 h. With no more events today it assumes a short trip
  out for the next hour (e.g. 12:22–13:22). Tune with `TRAVEL`, `LONG_BREAK`, `QUICK_TRIP` in
  `service.py`.
  Rules: dress for the coldest outdoor hour, and warn about a big temperature drop, rain (≥50% or
  ≥0.5 mm), wind (≥40 km/h) and heat (feels like ≥27°).
- **Widget:** `GET /widgets/weather` (htmx fragment). Marked stale if there's no successful run in 3 h.
- **Fragility:** low. It's a stable public API, and `parse()` skips hours with null values.

## Air quality

Same run, second request: `air-quality-api.open-meteo.com` (CAMS Europe forecast, free, no key)
→ `weather_air` (PM2.5, PM10, European AQI). The card shows the current air. When the next 12 h
reach "poor" (AQI 60+), a 😷 line with what to do goes on the card, `/weather`, voice and the
briefing. At "very poor" (80+) there's also a Telegram alert, once a day. If that API fails, the
weather still updates. CAMS is a model at ~10 km: a stove smoking next door won't show up.
