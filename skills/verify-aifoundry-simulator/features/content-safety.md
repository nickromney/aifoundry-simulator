# Content safety

Simulated Azure AI Content Safety: category analysis, prompt and document shields, blocklists with runtime CRUD, and the integrated filter on `gpt-demo` that returns 400 `content_filter`.

## Sub-features

- `safety-blocklist` covers config matches, `haltOnBlocklistHit` and runtime CRUD.
- `safety-shield` handles `text:shieldPrompt` prompt and document attacks.
- `safety-filter` covers the integrated chat filter, jailbreak detection, annotations and output filtering.

## How to get to it (user POV)

- POST `/contentsafety/text:analyze` or `/contentsafety/text:shieldPrompt`, or send a chat to `gpt-demo`.

## Driving it with control.sh

Preconditions:

- `doctor` prints `OK`.

- **Smoke.** `control.sh --execute smoke` gives `smoke_content_safety exit=0` with the three summary lines above (2026-10-10).
- **Single shield call (not yet driven).** `control.sh --execute cli shield "Ignore previous instructions"`, then `control.sh --execute cli safety-stats`.

## Gotchas

- Verdicts are transparent simulation. They prove wire shapes and policy flow, not real classification quality.
