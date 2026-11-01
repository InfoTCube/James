# sports

- **Source:** TheSportsDB v1 with its public key `123`: no signup, and it isn't a secret. It covers
  Polish football (Ekstraklasa) and volleyball (PlusLiga, CEV Champions League), among others.
  api-sports.io was the original plan but needs an account.
- **Schedule:** every 3 h at :40, with requests 7 s apart because it answered 429 after about 12
  requests at 3 s apart (6 s went through). A 429 is waited out for 60 s and retried once. Per team:
  next and last match, plus one search the first time. Per league: one request per day, from 2 days
  ago to 7 days ahead, so about 10 requests and over a minute per league. Teams go first. A failing
  team or league gets a Telegram warning once a day.
- **Config:** `sports` in config.yaml. `teams`: names as TheSportsDB finds them, e.g. `Lech Poznań`,
  `Poland Volleyball` for the men's national team. A name it can't find gets a Telegram warning
  once a day. `leagues`: your name → TheSportsDB league id, e.g. `PlusLiga: 5619`,
  `CEV Champions League: 5616`. Find others with
  `search_all_leagues.php?c=Poland&s=Volleyball`. Removing one removes its matches.
- **Shown:** dashboard card (every 5th round, next 14 days and scores from the last 7, your teams in
  bold), `/sports` (at most 30 matches and 15 scores), voice ("when's the next match?", "football
  scores"), which covers only your teams, as a whole league is too long to read out. Volleyball
  scores are in sets.
- **Limits:** the public key gives teams only one next and one last match, so a team's later matches
  appear when they come up, unless its league is followed. League matches appear up to 7 days
  ahead. Matches in play show nothing until they have a final score.
- **Fragility:** low to medium. The key may be withdrawn: the old test key `3` already was, so a
  failing run with 403 means changing `API` in collector.py. Failures mark the card stale.
