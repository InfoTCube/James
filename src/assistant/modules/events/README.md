# events

- **Sources**, all fetched by one collector (`events`) so duplicates can be dropped before the
  alert. A site that fails is skipped (Telegram warning, once a day per site); the run only
  fails if all of them do:
  - Meetup: `meetup.com/find/?location=pl--<city>&categoryId=546` (Technology) per city, the
    `__APOLLO_STATE__` in `__NEXT_DATA__`. Only the first ~15 events of the page; exact start
    times and the group name (used as topics).
  - Crossweb: `crossweb.pl/wydarzenia/<city>/` per city, plus `/wydarzenia/online/`. HTML rows
    (regex); dates without a year (a date >60 days past is next year's), kind, topics, free/paid.
    Also covers Polish hackathons.
  - Devpost and MLH were dropped (2026-10): with online hackathons unwanted, they only listed
    in-person hackathons abroad.
- **Schedule:** daily at 09:40 (and on worker start).
- **Config** (`events` in config.yaml): every event in one of `cities` matches, hackathons too;
  online ones (`online: true`) only with a `keywords` word in the title, kind or topics (whole
  word, case/diacritics ignored), and never online-only hackathons; nothing with an `exclude`
  word. Empty `cities` and `online: false` = off.
- **Stored:** only matching events that haven't started yet when first seen. Deleted a week after
  they end.
- **Duplicates:** Crossweb lists most Wrocław Meetup events. Key (`service.dedupe_key`): start day
  + title words in any order, case/diacritics ignored. The first site's entry is kept (Meetup —
  it has start times — before Crossweb). Missed: reworded titles.
- **Alerts:** one Telegram message per run with all new matches, soonest first (as many as fit in
  4096 characters, then "…and N more in /events"). `/events` lists the next 30 days.
- **Dashboard:** the Events card is in every 5th round of the rotation (`data-every="5"`), always
  one tap away in the rail. Tapping an event sends it with its link to Telegram
  (`POST /api/events/send/<id>`).
- **Fragility:** both are page scraping. Parsers are covered by
  fixtures in `tests/fixtures/events/`.
