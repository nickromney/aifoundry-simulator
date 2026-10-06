# Agent operating model

This is the navigation and control model for an agent operating or changing
Foundry. The agent is an external operator; agent hosting remains deferred.
The aim is accurate decisions per unit of effort: acquire the evidence needed
for the next decision, change the smallest owning layer, and retain verified
lessons where the next operator can find them.

## The abstraction chain

| Layer | Question answered | Authority / implementation | Evidence |
| --- | --- | --- | --- |
| Intent | What outcome must change? | Task acceptance criteria | Concrete before/after request or operational condition |
| Contract | What behavior may a client rely on? | [SCOPE](SCOPE.md), [contract matrix](../contracts/contract_matrix.yml) | Owning pytest markers; wire assertions |
| Resource | What identities and policies are configured? | JSON → `app/config.py` | Config validation; management status/deployments |
| Instance | What mutable state exists now? | `app/state.py` and its stores | Cache/safety observations; Responses lifecycle; blocklist reads |
| Execution | How does a request transform state and produce output? | Routers → `app/pipeline.py` → engine/stores | Status, headers, usage, filter annotations, SSE |
| Composition | Which service owns the observed result? | Foundry + optional APIM | Same request direct and through gateway |
| Learning | What can future work reuse? | Regression tests, contracts, scoped docs, ADRs | Reproduction and verification record |

Links run both ways. A symptom leads down to an owning module; a changed module
leads up to the affected contract and acceptance criterion. Keep deployment
identity consistent across these links. UI and CLI are projections of the same
HTTP controls, with no independent policy engine.

## Orient, predict, act, verify, retain

1. State the outcome and its acceptance evidence. Read README/build commands,
   then only the branch of documentation relevant to the task (table below).
   Completion: identify the owning layer and a reproducible condition.
2. Identify the target base URL and required credential role. For a known
   symptom, use the narrow `status`, `deployments`, `cache-stats`, or
   `safety-stats` command first. When relationships across configuration and
   runtime state matter, use `uv run --extra dev foundrysim inspect`. The command
   performs six GETs and emits one JSON object with a versioned envelope,
   per-observation paths/status/body, `complete`, and `atomic: false`.
   Exit 1 means at least one HTTP or transport failure. Health alone does not
   prove management authorization or inference readiness. Completion: account
   for failures and missing evidence before choosing a control.
3. Predict the effect, lifetime, and verification of one bounded action. Choose
   an isolated app/fixture when the experiment must preserve the running lab.
   Completion: name the expected observation and the state at risk.
4. Execute at the owning layer, then compare the actual evidence with the
   prediction. Preserve error envelopes and streamed terminal events alongside
   text. Completion: acceptance criteria pass, or record a falsified hypothesis
   and return to observation.
5. Retain a confirmed lesson as a regression test or update to its authoritative
   doc. Record assumptions, command, result, and limits when measurements matter.
   Completion: the next operator can reproduce the finding without chat history.

Inspection is sequential, not a transaction, and cannot prove which worker
answered each request. Recheck relevant observations after mutation; use a
single-worker isolated app for state-sensitive experiments. The report contains
server-returned metadata, not a full config export or a Responses/blocklist
inventory. It omits request credentials and transport exception text. Cache statistics include recent prompt excerpts; keep reports containing
sensitive input in local temporary artifacts. Review server-returned material
before sharing it. A valid envelope can contain failed
observations; consumers must inspect `complete` and individual statuses.

## Choose a control by lifetime

| Desired change | Control | Lifetime / verification |
| --- | --- | --- |
| Send a request | Inference or Content Safety HTTP; thin CLI | May populate caches, counters or Responses; inspect wire evidence |
| Change blocklist behavior now | Content Safety CRUD | Current app only; read back and test affected filter |
| Remove cached answers | `foundrysim cache-flush` | Entries cleared; counters/history retained and flush event appended; verify stats and next miss |
| Change deployment/filter/cache configuration | Edit source JSON; recreate app | All process-local state is lost; inspect loaded settings and test behavior |
| Change Python behavior | Edit code; focused tests; rebuild/recreate container | Running image changes only after rebuild; verify actual host port |
| Change gateway routing/budgets | APIM controls/configuration | Independent gateway lifecycle; verify direct and forwarded request |

Configuration is desired state; runtime CRUD is ephemeral state. Save intended
blocklist changes into source configuration explicitly when they must survive
recreation. Read [SIMULATOR-DESIGN](SIMULATOR-DESIGN.md) for full state ownership,
network topology, restart choices, and future console requirements.

## Diagnosis and selective reading

| Symptom / task | Read / inspect first | Discriminating experiment |
| --- | --- | --- |
| Routing or unsupported request | [MODEL-SERVING](MODEL-SERVING.md), router, contract owner | Same payload using actual deployment name; inspect error code/parameter |
| Unexpected answer or cache reuse | [SEMANTIC-CACHE](SEMANTIC-CACHE.md), cache headers/events | Isolated cold/warm pair; vary one prompt/option; compare score and usage |
| Filter rejection or changed verdict | [CONTENT-SAFETY](CONTENT-SAFETY.md), loaded policy and live blocklist | Standalone analysis plus integrated request; distinguish prompt/output |
| Lost Responses/blocklists/state | State lifecycle in shared design; `app/state.py` | Check app recreation/worker topology before changing persistence |
| Gateway discrepancy | Integration README and APIM ownership | Direct/forwarded pair; compare auth, headers, usage and terminal framing |
| Slow requests or resource use | [FOUNDRY-PERFORMANCE](FOUNDRY-PERFORMANCE.md) | Separate configured delay, cache path and measured compute |

For chat, reason through authentication/routing and validation → prompt safety
→ cache lookup → miss latency/generation → output safety/annotations → eligible
cache store → JSON/SSE. A hit skips generation/output filtering; prompt safety
still runs. Responses has its own path and store, with no semantic caching.
Embeddings also bypass chat caching. Trace the actual path before extrapolating
from another surface.

## Verification budget and durable learning

Start with the contract owner or focused regression. Run `make lint` and
`make test` for code changes. Compose edits also require `make compose-config`;
container/network claims require host-port smoke. APIM claims require pairing
smoke, and gateway budget claims require the sibling's budget checks. Smoke
requests mutate lab state; pairing smoke clears Foundry's cache. Record skipped
checks and their limits. A unit test does not establish container connectivity,
and deterministic echoes do not establish model or moderation quality.

Keep temporary experiment data in ignored `.run/`; keep reusable fixtures,
regressions and measured evidence in their existing authoritative locations.
Promote a finding only after reproducing it. Update AGENTS.md only for a concise,
project-wide rule; put procedures here or in the feature doc. Use an ADR when
ownership or scope changes. Prefer an existing contract ID for stronger evidence;
add a new ID when a new behavior becomes a promise. Avoid parallel catalogs of
routes, defaults, commands or test names: inspect code, CLI help and the matrix.

## Implementation plan and completion record

This revision is intentionally bounded to the existing simulator's control
surface. Each item is independently reviewable and introduces no new service.

- [x] Map abstractions, owners, lifetimes, execution paths, and evidence here.
- [x] Add selective navigation from README and AGENTS.md; connect shared design
  and feature guidance to this model rather than duplicating their facts.
- [x] Implement `foundrysim inspect` as read-only HTTP composition, with explicit
  partial failure, non-atomicity, and one machine-readable envelope.
- [x] Extend CLI contract ownership and tests for retained cache state, denied
  management access, transport failure, and non-JSON upstream failure.
- [x] Run lint/full tests and review the final diff. `make lint` passed;
  `make test` passed all 118 tests with 85.92% branch-enabled coverage;
  `git diff --check` passed. Used `UV_CACHE_DIR=/tmp/foundry-uv-cache`
  for sandbox-compatible tooling. Container/APIM smoke was not run: this
  revision changes CLI composition and documentation, not container routing.

Future capability proposals must start with a demonstrated decision that the
existing evidence cannot resolve. A unified trace would need request correlation,
bounded retention, redaction and cross-service ownership; persistent state would
need explicit consistency/lifecycle contracts. These are design constraints for
separately scoped work, not promised features or dependencies of this plan.

## Sibling comparison — 2026-10-06

Reviewed the uncommitted APIM agent-system changes on top of `a2d38e0`:
`docs/AGENT-SYSTEM.md`, `docs/AGENT-SYSTEM-PLAN.md`, the CLI/test diff, and
roadmap integration. Their authority chain and learning loop agree with this
model. APIM adds offline discovery/request previews and effective-policy
inspection because its management plane authors and composes gateway policies.
Foundry's current CLI principally sends inference and reads management evidence;
its bounded inspection command addresses a different demonstrated gap. Keep
these interfaces appropriate to their service ownership rather than duplicating
APIM policy concepts here.

Adopted APIM's narrow-observation-first guidance and explicit sensitive-evidence
handling. This was a design/diff reasonability comparison, not independent
execution of APIM's reported checks or a full sibling code review. The sibling
working tree was read only; its evolving changes are evidence, not dependencies.
