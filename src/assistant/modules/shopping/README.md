# shopping

- **What:** a shopping list. `/list mleko, chleb` adds (an item already there isn't added twice),
  `/list` shows it, `/got mleko` (the start of it is enough) or `/got 3` ticks it off,
  `/got all` empties it. Voice: "add milk and bread to my shopping list", "what's on my
  shopping list", "remove bread from my shopping list", "clear the shopping list". Dashboard
  card, hidden when the list is empty.
- **Promotions:** an item matching a current Biedronka offer gets 🐞 with the cheapest price.
  Only offers already found for your `/buy` products count (same word matching as the leaflets,
  after the nightly Claude check): to have the leaflets searched for something, `/buy` it too.
- **Storage:** `shopping_items` in the local DB. No schedule, no network.
- **Later:** sync with Google Tasks (so the phone has it) when the Google login is redone for
  `email`.
