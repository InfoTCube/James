# mpk

- **Source:** Wrocław GTFS timetable from the open data portal (moved in April 2026):
  - catalogue: `https://api.open-data.cui.wroclaw.pl/od2/6/` returns `{"pliki": [file ids]}`
  - file: `https://open-data.cui.wroclaw.pl/hdb/download/{id}/`. The effective date is in the
    filename, e.g. `..._GTFS_26092026.zip`.
  - The old `www.wroclaw.pl/open-data/...` URLs return 403. The Mobility Database mirror stopped
    in Dec 2025.
- **Schedule:** daily 04:10. A new timetable appears roughly every 2 weeks, and file ids aren't in
  date order. The collector HEADs the newest few ids, picks the newest file already in effect
  (one published early isn't used before it starts), and skips the import if that file is
  already imported. An import replaces all `mpk_*` tables, takes ~20 s, and makes the DB
  ~90 MB.
- **Config:** `places` in `config.yaml` maps each alias to stop *names*. Names stay stable across
  timetable versions; IDs may not. All platforms with that name are used. `walk_minutes` is the
  walk to the stops. `home` is the trip origin.
- **Matching** (`service.resolve`): a place alias or keyword, then a stop name inside the text,
  then a fuzzy match on stop names. Case and Polish diacritics are ignored. A matched stop that
  belongs to a place resolves to the place.
- **Connections** (`service.connections`): direct trips only, latest arrival first. For each trip
  it boards at the last home stop and gets off at the first destination stop. Trips after
  midnight that belong to the previous service day aren't considered.
- **Widget:** `GET /widgets/mpk` shows the next event with a location starting within 2 h
  (`LOOKAHEAD` in `routes.py`): leave-by time and 3 options. Marked stale after 2 days without a successful run.
- **Expired timetable:** each file covers ~2 weeks. If no newer one arrives in time, trips after
  the last covered day use the services that normally run on that weekday (`calendar.txt` without
  holiday exceptions), and the card warns "Timetable ended DD.MM".
- **Live delays (estimates):** there's no official real-time feed for Wrocław.
  `client.vehicle_positions()` POSTs to `https://mpk.wroc.pl/bus_position` (form
  `busList[tram][]=16&busList[bus][]=145`; response `x` = **lat**, `y` = **lon**, `k` = MPK run
  id, not a GTFS trip id), cached 20 s, `[]` on failure. For each option on the card, a vehicle
  of its line within 150 m of the trip *before* your boarding stop, **moving towards it** (the
  `Tracker` compares readings between the card's 1-min refreshes), gives
  delay = now − schedule at its position (interpolated between stops). Plausible range −2..+20
  min; the closest to on-time wins. The first estimate appears on the second refresh. The open
  data vehicle table is only an hourly snapshot, so it's unusable.
- **Not yet:** transfers. Live delays are only computed while the dashboard card is visible.
