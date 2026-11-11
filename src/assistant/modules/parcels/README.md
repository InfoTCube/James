# parcels

- **Source:** InPost's public tracking API (ShipX, `api-shipx-pl.easypack24.net/v1/tracking/{n}`,
  no key) and its status list (`/v1/statuses?lang=en_GB`) for readable titles.
- **Use:** `/track <24-digit number> [name]` (spaces in the number are fine; several numbers in
  one message work), `/track` alone lists them, `/untrack <name or last digits>`. Voice: "where
  are my parcels". Dashboard card (hidden when nothing is tracked).
- **Schedule:** every 20 min, 07:00–22:40. `/track` also checks right away.
- **Alerts:** a Telegram message when a parcel reaches a status in `service.ALERT` (out for
  delivery, in the locker, with its address, reminder, expired, problems). Sorting centres etc.
  only show on the list. Finished parcels (delivered, returned, cancelled) leave the list after
  2 days.
- **Fragility:** the response shape comes from InPost's docs (`tests/fixtures/parcels/tracking.json`
  is built from them), not a recorded live parcel: check `client.parse` against a real number.
  Other carriers aren't supported.
- **Later:** catch tracking numbers from emails automatically (`service.find_numbers`), once the
  `email` module exists.
