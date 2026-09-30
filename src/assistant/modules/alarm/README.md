# alarm

- **Automatic alarm:** `before_leaving_minutes` (40) before you first have to leave home that
  day (`mpk.service.first_departure`). There's none if that's at or after `latest` (11:00: you're
  up anyway) or if nothing that day needs leaving home. Config: `alarm:` in `config.yaml`.
- **Planner** (`AlarmPlanner`, every 5 min): creates, moves or removes today's and tomorrow's
  automatic alarm as the calendar changes. A cancelled or rung alarm is never touched again.
- **Ringer** (`AlarmRinger`, on the minute): a due alarm (up to 10 min late, e.g. after a
  restart) starts **ringing**:
  - dashboard: full-screen bell, beeping (Web Audio, no sound files), **Stop** and
    **Snooze 10 min** buttons. It polls `/api/alarm/state` every 3 s.
  - Telegram: a message with the same two buttons (the bot handles them).
  Nobody answers for 3 min (`RING_FOR`) → it stops by itself. Snooze rings again 10 min later.
- **After stopping:** the briefing is shown on the dashboard for 3 min (click to close), read
  aloud with the browser's speech voice, and sent to Telegram.
- **Kiosk sound:** browsers block sound until the page is clicked. Start the kiosk with
  `chromium --kiosk --autoplay-policy=no-user-gesture-required http://localhost:8000`. In a
  normal browser, click the dashboard once (it shows a hint while sound is blocked).
- **Briefing** (`service.briefing`): weather and what to wear, next departure with the tram,
  today's events. Also on demand via `/briefing`.
- **API for the bot now and voice later:** `parse_time("7:30", now, tz)`,
  `add_alarm(session, at, label)`, `upcoming_alarms(session, now)`,
  `cancel_alarm(session, id)`, `due_alarms(session, now)`. Bot: `/alarm 7:30 [label]`,
  `/alarms`, `/alarm_off 3`.
- **Actions** (`actions.py`: `ring`, `stop`, `snooze`) are shared by the worker, the dashboard
  buttons, the Telegram buttons and later voice ("stop", "snooze").
- **Not yet:** nicer speech with Piper (voice service), and choosing the sound.
