# news

- **Sources:** the RSS/Atom feeds in `news.feeds` in config.yaml (name → URL), parsed with the
  standard library (`xml.etree`). Defaults: tuwroclaw.com (Wrocław), INFO Wieluń (Wieluń), Polsat News (Poland), BBC World,
  Hacker News, the .NET blog.
- **`news` collector**, hourly at :30: new headlines into `news_items` (title, link, feed,
  published), kept 3 days. A failing feed is skipped (Telegram warning once a day per feed); the
  run only fails if all do.
- **`news_digest` collector**, 01:45–05:45 hourly: once per night, the newest 25 headlines per feed
  from the last 24 h go to `core.ai` in one call (Haiku, ~2k input tokens), which writes up to 8
  Polish bullet points (`PROMPT` in collector.py; the briefing reads them with the Polish voice,
  see `core.tts.is_polish_sentence`), stored in `news_digests` for that day. If the
  AI fails, the run is recorded as failed and retried the next hour; until a digest exists,
  everything shows raw headlines instead. `core.ai` only calls Claude at night anyway.
- **Shown:** the dashboard News card (every 3rd round): today's digest, then the latest headlines
  (two per feed, tap one to get its link on Telegram). `/news`: the same. The morning briefing
  reads the digest's top 3 points.
- **Fragility:** low. Feeds change URL now and then (the warning says which). The digest's quality
  depends on the prompt.
