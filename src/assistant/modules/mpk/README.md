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
- **Not yet:** live delays (vehicle positions at `mpk.wroc.pl/bus_position`), transfers, and street
  addresses. "Legnicka 5" matches nothing; it would need geocoding.
