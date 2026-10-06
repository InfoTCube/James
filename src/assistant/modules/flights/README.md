# flights

- **Watches** are set from Telegram and stored in `flights_watches` (not config.yaml):
  - `/flight_add <where> <dates> <nights> <max price>`, e.g. `/flight_add any 1.11-30.11 2-4 300`,
    `/flight_add BCN,MAD 15.12-6.01 5-9 600`, `/flight_add LTN 20.11 oneway 150`. Where: airport
    codes or `any`. Dates: the whole trip falls within them (no year = the next such day). Nights:
    `3`, `2-4` or `oneway`. Price: PLN, one person, there and back together.
  - `/flights`: each watch with the 3 cheapest trips found (✅ = under its price).
  - `/flight_price 3 250`: new price for watch #3 (re-checked within 15 min).
  - `/flight_del 3`. A watch whose last possible day out has passed is removed (with a message).
- **From WRO**, direct flights only (`ORIGIN` in collector.py):
  - Ryanair: `ryanair.com/api/farfnd/v4/roundTripFares` (or `oneWayFares`), one request per watch
    gives the cheapest trip to every destination in the window.
  - Wizz Air: the API base URL (it changes with each release) from `apiUrl` in the home page;
    destinations from `/asset/map`; daily one-way prices from `POST /search/timetable` per route
    and direction, in ≤42-day pieces, 1 s apart (~1 min for "any"); the cheapest pair of days
    with the right number of nights is put together here. Days marked `checkPrice` have no price
    and are skipped.
- **Schedule:** the collector runs every 15 min and checks each watch that hasn't been checked for
  12 h (so twice a day), or at once when new or repriced. Only 07:00–22:00 (`DAY`), so alerts
  never come at night: a check due at night waits until 07:00.
- **Stored:** `flights_fares`, the cheapest trip per watch, airline and destination at the last
  check (above the price too, for `/flights`). A failing airline keeps its previous fares.
- **Alerts:** one Telegram message per run with the deals: under the watch's price and not sent
  before, or 10% cheaper than when last sent (`DROP`), cheapest first.
- **Dashboard:** the Flights card lists the deals (fares under their watch's price), cheapest
  first; without deals it shows each watch and its cheapest fare so far, or how to add one. In
  every 5th round of the rotation (`data-every="5"`). Tapping a deal sends its booking link to Telegram
  (`POST /api/flights/send/<watch>/<airline>/<destination>`).
- **Fragility:** both are unofficial endpoints; the Wizz Air home page layout and timetable range
  limit can change. Parsers are covered by fixtures in `tests/fixtures/flights/`.
