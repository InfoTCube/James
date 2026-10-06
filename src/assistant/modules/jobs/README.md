# jobs

- **Sources**, all fetched by one collector (`jobs`) so duplicates can be dropped before the
  alert. A board that fails is skipped (Telegram warning, once a day per board); the run only
  fails if all of them do:
  - justjoin.it: `justjoin.it/api/candidate-api/offers?keywords=…`, newest 100 per keyword,
    all of Poland. Gives hybrid office days (`hybridWorkSchedule.officeDays`).
  - nofluffjobs: `POST nofluffjobs.com/api/search/posting`, per keyword, limited to the configured
    cities + remote. Hybrid office days are free text only, so unknown.
  - pracuj.pl: `it.pracuj.pl/praca/<keyword>;kw`, newest 50 per keyword, all of Poland; the
    listing JSON in `__NEXT_DATA__`. No office days in the listing.
- **Schedule:** every 4 h, 08:20–20:20.
- **Config** (`jobs` in config.yaml): `keywords` (title or skills, whole word), `exclude` (title),
  `seniority` (substring of the board's level, e.g. `junior` matches "Młodszy specjalista
  (junior)"; offers without a level pass), and where: any work mode in `cities`, remote from
  anywhere (`remote`), or hybrid in `hybrid_cities` with at most `max_office_days` a week (unknown
  office days pass and show as "hybrid (office days?)"). Empty `keywords` = off.
- **Stored:** only matching offers (`jobs_offers`), kept 120 days so they don't alert twice.
  Changing the config affects offers seen from then on.
- **Duplicates:** the same job on several boards (or justjoin.it's one listing per city) is
  alerted once. Key (`service.dedupe_key`): company without legal form ("Sp. z o.o.", "S.A.",
  "Poland"...) + title words in any order, minus gender tags like "(k/m/n)"; case and diacritics
  ignored. Only the first board's offer is stored (order: justjoin.it, nofluffjobs, pracuj.pl).
  Missed: reworded titles ("M365/SharePoint" vs "SharePoint/Microsoft 365").
- **Best fit (★/⭐):** `service.fit` gives a point each for a keyword in the title, a home city or
  remote (not just hybrid elsewhere), and a stated level matching `seniority`. All three = best
  fit; lists are sorted by points, then newest. Computed when shown, so config changes apply.
- **Hours:** "full-time" / "part-time OK" when the board says (justjoin.it `workingTime`,
  pracuj.pl `workSchedules`); B2B/freelance alone and nofluffjobs don't, so nothing is shown.
- **Alerts:** one Telegram message per run with all new matches, best fit first (as many as fit
  in 4096 characters, then "…and N more in /jobs"). `/jobs` lists matches from the last 7 days.
- **Dashboard:** the Jobs card is in every 10th round of the rotation (`data-every="10"`), and
  always one tap away in the rail. Tapping a job sends it with its link to Telegram
  (`POST /api/jobs/send/<id>`).
- **Fragility:** all three are unofficial endpoints; pracuj.pl is HTML scraping. Parsers are
  covered by fixtures in `tests/fixtures/jobs/`.
