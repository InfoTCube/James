# timers

- **What:** kitchen timers. Voice: "set a timer for 10 minutes (for the pasta)", "pasta timer
  8 minutes", "an hour and a half", "how much time is left", "cancel the pasta timer", and
  "stop" when one rings. Telegram: `/timer 10m pasta` (`1h30m`, `90s` work), `/timer` lists,
  `/timer_off [pasta]`.
- **Ringing:** on the dashboard (polls `/api/timers` every second): countdowns in the rail, and
  a done timer takes the screen with its own beep and a Stop button. It rings 3 minutes at most.
  Music gets quieter and the night screen wakes while it rings. No Telegram message: a timer is
  for when you're home. "stop" stops a ringing timer first, then the music; it never cancels a
  running timer.
- **Storage:** `timers_timers`; timers older than a day are deleted when you next stop one.
