# data-platform-infrastructure

## Jev

- Classify, route, rate or decide between fixed options with the `jev-decide` skill (TypeSafe Jev, `typesafe/jev-1.14` via OpenRouter).
- Jev decides, Claude writes. When Jev is uncertain (choice/score confidence < 0.6, noul between 0.2 and 0.8), decide yourself and say so.
- Before stating a version, API or advisory taken from a source, ask Jev (noul) whether that source supports the claim; if not, don't state it as fact.
- Everything sent to Jev leaves the machine: ask before sending anything private. Never print, copy or paste the API key; it lives in a gitignored `.env` (`OPENROUTER_API_KEY`).
