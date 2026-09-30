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
