---
name: jev-decide
description: >
  Lets TypeSafe's Jev decision model (typesafe/jev-1.14 via OpenRouter) make data-platform-infrastructure's
  typed decisions — risk tier, model, triage, merge go/no-go, final approval — and check
  that a claim is supported by the source it came from. Use whenever choosing between fixed
  options, or before asserting a version, API, advisory or behavior taken from a source.
---

# Jev decides

**Jev decides, you write.** Use Jev's answer as the decision and write everything around
it. When Jev is uncertain, decide yourself and say so. Everything in `state` leaves the
machine: ask before sending personal, customer or secret data. Never print or copy the API
key; the script reads `OPENROUTER_API_KEY` from the environment, else from the first `.env`
holding it in the working directory or any parent (it may sit outside the repo). Send a short
description of the change, never code, secrets or customer data.

The API is alpha: if a call fails unexpectedly, re-read
https://openrouter.ai/docs/guides/community/jev before guessing.

## Decision points

| Point | Question type | Options |
|-------|---------------|---------|
| Risk tier of a change | `choice` | `fast_path`, `standard`, `high_risk` |
| Model for a dispatch | `choice` | `haiku`, `sonnet`, `opus` |
| Merge gate | `noul` | "Do the review findings include anything that must block the merge?" |
| Final approval | `noul` | "Is every acceptance criterion met by the cited evidence?" |
| Claim grounding | `noul` | "Is the claim fully supported by the source alone?" |

## Ask

```bash
python3 .claude/skills/jev-decide/jev.py <<'EOF'
{
  "state": {"change": "...", "diff_summary": "..."},
  "questions": {
    "tier": {"type": "choice", "instructions": "Which risk tier fits this change?",
             "criteria": {"fast_path": "...", "standard": "...", "high_risk": "..."}},
    "blocking": {"type": "noul", "instructions": "Do the findings include anything that must block the merge?"}
  }
}
EOF
```

- `choice`: `criteria` maps each option to a one-line description that separates it from the others.
- `score`: `criteria` is an array, lowest level first. `noul`: yes/no, optional `true`/`false`
  criteria; it returns a probability and no confidence.
- Questions are answered independently; put all of them for one item in one call.
- Jev knows only `state`: put the evidence there as named fields.
- Claim check: `state` = `{"claim": ..., "source": ...}`, one `noul`.
- `--dry-run` prints exactly what would be sent, without sending; `--verbose` shows HTTP error bodies.

## Read the answer

Each answer carries `"uncertain"`: `choice`/`score` below confidence 0.6, or `noul` strictly
between 0.2 and 0.8. Raise the bar where a wrong call is expensive.

| Outcome | Do |
|---------|----|
| Confident | Act on Jev's answer; cite it in one line ("Jev: high_risk, 0.97") |
| Uncertain | Decide yourself and say why ("Jev unsure (0.52) → standard because …") |
| Claim not supported | Don't state it; find better evidence or mark it unverified |
| Exit 2 (malformed answer) | Decide yourself and say Jev's answer was unusable |
| Exit 3 (no key), 4 (API error) or 5 (bad input) | Decide yourself and say Jev wasn't available |
