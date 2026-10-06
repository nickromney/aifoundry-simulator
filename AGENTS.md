# Repository Guidelines

Use this file for durable, concise guidance for coding agents in this repository.

- Before changing code, read `README.md` and the nearest package/build manifest for the commands and constraints that apply.
- Add confirmed project-specific commands, conventions, and constraints here when they become durable.

## Foundry conventions

- For system design, operational diagnosis, or control changes, read `docs/AGENT-OPERATING-MODEL.md` for layer ownership, evidence, and the observe/act/verify loop. Use `foundrysim inspect` for read-only runtime orientation; its observations are non-atomic.

- Read `docs/SIMULATOR-DESIGN.md` before changing APIM integration, runtime state, caching, or operator UI; it carries the sibling project's design and verification lessons.
- Keep wire behavior classified in `docs/SCOPE.md` and `contracts/contract_matrix.yml`, with owning pytest contract markers. Deployment names are inference routing identities; model names are metadata.
- Keep mutable stores per app instance. Runtime blocklist edits, caches, counters, and Responses are process-local; config changes require app recreation.
- Run Python tooling with `uv run --extra dev`. Use `make lint` and `make test` (branch coverage gate: 75%); `make compose-config` for Compose changes. Verify host-port smoke with `make smoke` and `make -C examples/apim-integration smoke`; the sibling's `make smoke-ai-foundry` checks gateway token budgets.
- Before optimizing, read `docs/FOUNDRY-PERFORMANCE.md`; profile, preserve golden behavior and measure one lever at a time. Separate simulated latency, cache hits, and actual compute cost.
- Keep request-option cache isolation inside deployment capacity/TTL limits; validate JSON types before set membership and apply the earliest stop/token boundary.

## Codex workflow

- Keep this file short, concrete, and repo-specific. Capture layout, commands, conventions, constraints, and done criteria; move repeatable procedures to scoped skills/docs.
- For each task, state the goal, relevant context/files, constraints, and verification criteria. Plan complex or ambiguous work before editing.
- Keep one thread per coherent outcome. Read only relevant files; delegate bounded exploration/tests when useful, and use worktrees for parallel work.
- Verify changes with focused tests and applicable lint, formatting, type checks, builds, and diff review; report checks run or skipped.
- Prefer least-privilege permissions and dry-runs. Add MCP/tools only when they remove a real repeated loop.
- Use background or scheduled work for long-running or recurring tasks instead of continuous polling.
- After a repeated mistake or correction, update this file with the smallest actionable rule that would prevent it.

Reference: https://learn.chatgpt.com/guides/best-practices
