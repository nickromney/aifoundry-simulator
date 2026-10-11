# Semantic cache

Chat deployments with caching enabled return a cached completion for a semantically similar prompt (score under the 0.1 threshold, TTL 300 s), with `x-semantic-cache` response headers and counters under management.

## Sub-features

- `cache-miss` gives `x-semantic-cache: miss` on first sight.
- `cache-hit` gives `x-semantic-cache: hit` and `x-semantic-cache-score` for a paraphrase.
- `cache-stats` returns hits, misses, lookups and per-deployment entries.
- `cache-flush` clears entries.

## How to get to it (user POV)

- POST to `/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21` with `api-key`.

## Driving it with control.sh

Preconditions:

- `doctor` prints `OK`, and `control.sh --execute cli cache-flush` has run.

- **Miss then hit, via `make verify`** (run `20261010T114340Z`, 2026-10-10): the capital prompt gave `x-semantic-cache: miss` and the lowercase paraphrase gave `x-semantic-cache: hit`, both status 200.
- **Miss then hit, by hand.** Send the two requests in SKILL.md. The first gives `status=200` and `x-semantic-cache: miss`. The second gives `x-semantic-cache: hit` with `x-semantic-cache-score: 0.005168` (2026-10-10).
- **Counters.** `control.sh --execute cli cache-stats` shows `"hits": 1`, `"misses": 1`, `"lookups": 2` and `"entries": 1`.

## Gotchas

- `gpt-nofilter` has caching off, so expect no cache headers there.
