# AI Foundry simulator verification map

This directory is the maintained source for verifying the simulator's HTTP surface. Read this index, then use the matching feature file.

## Baseline preconditions

- `control.sh --execute launch` printed `ready` and `doctor` prints `OK binding=127.0.0.1:8120`.

## Driving conventions

- Make requests through `control.sh --execute request` or the `foundrysim` CLI through `control.sh --execute cli`.
- Use the documented demo keys only.

## Proof and skip reporting

- HTTP proof is the status, `x-*` headers and body. CLI proof is the transcript.
- Name the APIM integration example (`examples/apim-integration`) as skipped. It needs the sibling apim-simulator.

## Feature entry contract

Each feature file has an H1, one paragraph, and then `Sub-features`, `How to get to it (user POV)`, `Driving it with control.sh` and `Gotchas`.

## Features

- [Semantic cache](./semantic-cache.md): miss, then a paraphrase hit, with counters. **Driven 2026-10-10.**
- [Content safety](./content-safety.md): blocklists, prompt shields and the integrated filter. **Driven via the repo smoke script, 2026-10-10.**
- [Chat inference](./chat-inference.md): chat, streaming and management gating. **Driven via the repo smoke script, 2026-10-10.**
