# meals

- **What:** 3 dinner ideas a week built around this week's Biedronka promotions on your `/buy`
  products. Dashboard card ("Dinner ideas", every 5th round), `/meals`, voice ("what should I
  cook?", "dinner ideas").
- **Source:** the *checked* offers from `biedronka` (after its nightly Claude check), so wrong
  matches never reach it. No offers: no ideas that week.
- **AI:** one Claude call a week (`meals_ideas`, keyed by the week's Monday). It runs nightly at
  05:55 after `biedronka_check` and only calls when this week has no ideas yet, so a failed call
  is retried the next night. Config: `meals.taste` (free text, e.g. "quick, no mushrooms").
- **Fragility:** the answer must be 3 lines "name | uses | how"; other lines are ignored.
