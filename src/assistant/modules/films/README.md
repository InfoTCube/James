# films

- **Source:** JustWatch's GraphQL endpoint (`apis.justwatch.com/graphql`), the one justwatch.com
  itself uses. No key or account. It covers subscription streaming in Poland directly.
- **Schedule:** nightly at :15 between 01:15 and 05:15, until the week has picks. The first run of
  the week (Monday 01:15) makes one or two requests for 40 popular films and replaces last week's
  unpicked ones.
- **AI:** one call a week (Haiku) picks 3 from the candidates, each with a one-sentence reason.
  Past picks are kept in `films` and never suggested again. If the AI fails, the card and `/films`
  show the best rated candidates, and the next run retries.
- **Watched films** (`films_watched`): marked with `/watched Inception` (alone: the list),
  `/unwatched Inception`, voice ("I've watched Inception") or the card's "✓ Watched" button. They're
  never suggested, a marked pick is replaced on the card right away, and the newest 100 titles go to
  the AI to judge your taste. Plain title matching ignores case and diacritics; the AI is told to
  catch other titles and translations, so without AI a differently spelled title can still appear.
- **Config:** `films` in config.yaml: `taste` (free text for the AI), `providers` (services you pay
  for, as JustWatch names them: Netflix, HBO Max, Disney Plus, Amazon Prime Video, Polsat Box Go,
  SkyShowtime, Player, CANAL+, MUBI, CDA Premium…; empty = any), `min_rating` (IMDb), `min_votes`
  (IMDb).
- **Shown:** dashboard card (every 10th round, tap a film to get its link on Telegram), `/films`,
  voice ("any good films?").
- **Fragility:** medium. The endpoint is unofficial and can change without notice; a failed run
  shows as stale on the card, and the fix is in `POPULAR` / `parse_films` in collector.py. An
  unknown service name only logs a warning, unless none of them are known, which fails the run.
