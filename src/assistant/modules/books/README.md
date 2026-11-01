# books

- **Source:** Open Library search API (`openlibrary.org/search.json`), no key. Google Books isn't
  used: Open Library has the ratings and languages this needs.
- **Schedule:** nightly at :25 between 01:25 and 05:25, until the month has picks. The first run of
  the month fetches the 25 best rated books per subject, with one request per subject, and replaces
  last month's unpicked ones.
- **AI:** one call a month (Haiku) picks 3 from the candidates, each with a one-sentence reason.
  It only sees title, author, year and rating. Past picks are kept in `books` and never suggested
  again. If the AI fails, the card and `/books` show the best rated candidates, and the next run
  retries.
- **Read books** (`books_read`): marked with `/read Atomic Habits` (alone: the list), `/unread
  Atomic Habits`, voice ("I've read Atomic Habits") or the card's "✓ Read it" button. They're never
  suggested, a marked pick is replaced on the card right away, and the newest 100 titles go to the
  AI to judge your taste. Plain title matching ignores case and diacritics; the AI is told to catch
  translations (Atomowe nawyki), so without AI a differently spelled title can still appear.
- **Config:** `books` in config.yaml: `subjects` (Open Library subject names; empty = off), `taste`
  (free text for the AI), `languages` (ISO 639-2 codes such as `eng`, `pol`; empty = any),
  `since_year`, `min_ratings`.
- **Shown:** dashboard card (every 10th round, tap a book to get its link on Telegram), `/books`,
  voice ("any good books?").
- **Fragility:** low. Ratings on Open Library come from few people, so `min_ratings` matters more
  than the score. Language codes cover any edition, not necessarily a Polish one you can buy.
