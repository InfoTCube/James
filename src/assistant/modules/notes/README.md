# notes

- **Storage:** `notes_notes` in SQLite. `folded` is the text lowercased and without Polish
  diacritics, so search ignores both ("zolw" finds "Żółw").
- **Service (bot now, voice later):** `add_note(session, text, now)`,
  `search_notes(session, query="", limit=10)` (every word must match; empty query = newest),
  `delete_note(session, id)`.
- **Bot:** `/note <text>` saves, `/notes [words]` lists newest or searches, `/note_del 3`.
- **Not yet:** Markdown export and a dashboard card. Add them if you need them.
