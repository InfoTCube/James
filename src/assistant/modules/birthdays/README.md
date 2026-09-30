# birthdays

- **Source:** Google Calendar's contact birthdays (`eventType: birthday`). The calendar module
  already fetches them (next 14 days) and keeps them off the calendar card. No separate Google
  Contacts access is needed.
- **Names:** `Mateusz's birthday` → Mateusz, `Urodziny Zosi` → Zosi; other titles as they are.
- **Reminders** (`BirthdayReminder`): 18:00 the day before and 09:00 on the day, via Telegram,
  each sent once.
- **Also:** dashboard card (next 7 days, hidden when empty), a line in the morning briefing,
  `/birthdays` (next 14 days).
