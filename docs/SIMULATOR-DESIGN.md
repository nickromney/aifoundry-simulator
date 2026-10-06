# Shared simulator design and operating lessons

Transferred from the sibling APIM simulator on 2026-10-05. This document
records choices grounded in that project's implementation and verification;
future UI guidance below is a design contract, not an implemented console.

## Coherent system model

The [agent operating model](AGENT-OPERATING-MODEL.md) links intent, contracts,
configuration, runtime state, execution, composition and retained evidence.
Use it when choosing a control or investigating a symptom. This document owns
the shared lifecycle and integration constraints; feature guides own their
behavior details. Future operator interfaces should expose evidence and action
lifetime together, preserve partial failures, and use the same HTTP authority
as the CLI. A console must make an observation distinguishable from a prediction
and refresh affected evidence after a mutation.

## Service ownership and compatibility

APIM owns gateway routing, subscriptions, policy execution, token budgets,
and gateway traces. Foundry owns deployment routing, deterministic model
outputs and usage, service-side semantic caching, content filtering, and
the standalone Content Safety endpoints. Foundry management uses its own
admin key; gateway subscriptions do not grant Foundry management access.

Keep the public request contract separate from the teaching implementation.
`docs/SCOPE.md` and `contracts/contract_matrix.yml` classify supported,
adapted, and deferred behavior. Every implemented contract has an owning
pytest contract marker. Reject unsupported controls explicitly. Preserve
deployment names as routing identities and response model values; the
underlying model name remains metadata.

Use the same inputs against direct Foundry and APIM-fronted Foundry when
checking compatibility. Assert status, Azure error shape, usage, headers,
stream events, and terminal markers, as well as text. Gateway budgets must
consume Foundry's returned usage. Filtering errors and cache headers must
survive forwarding. A successful local request proves the tested subset,
not complete Azure fidelity.

The existing shared-network topology uses `aifoundry`, with backend address
`http://aifoundry-simulator:8000`. Host clients use port 8020 by default;
container localhost refers to that container. Start Foundry first, then
APIM; stop APIM first so its attachment does not prevent network removal.
See [the integration guide](../examples/apim-integration/README.md).

## State lifecycle and the cheap local loop

| State | Owner and source | What survives a restart? |
| --- | --- | --- |
| Deployments, auth, cache/filter configuration | JSON loaded by `create_app` from `AIFOUNDRY_CONFIG_PATH` | Source file survives; configuration is reloaded |
| Content Safety blocklist CRUD edits | Per-app `AppState.blocklists`, shared with loaded filter configuration | Process-local edits are lost |
| Semantic entries, counters, events and safety stats | Per-app runtime stores | Lost |
| Stored Responses and input items | Per-app response store | Lost |
| APIM management edits | APIM runtime config, normally its container tmpfs | Governed by APIM lifecycle; independent of Foundry |

Each test should construct its own app and runtime stores. Treat mutable
response bodies, filter metadata, and cache records as owned values: returned
objects must not let callers mutate future requests. A cache flush clears
cache state; it is not a deployment reload or a reset of every service.
Multiple workers have separate stores and do not provide shared persistence.

Reuse running containers for requests and runtime blocklist changes. Editing
Python or baked-in example configuration requires rebuilding/recreating;
editing a bind-mounted config requires app recreation to reload it. `make up`
requests a build; `docker compose up --detach --wait` can reuse existing
images. `make restart` recreates without rebuilding. Keep the chosen Compose
overlays and environment consistent with the original stack. Back up desired
configuration before a reset; process-local CRUD is not automatically saved.

The basic request path runs locally once dependencies and images exist.
Initial installs/builds contact registries; configured external clients and
APIM backends can still use the network. The local stack's hardening uses
non-root images, a read-only filesystem, dropped capabilities and tmpfs.

## Interface design inherited from APIM

Foundry currently exposes HTTP APIs and `foundrysim`, with a JSON entrypoint
index at `/`. There is no browser operator console. If a console is added,
reuse APIM's final Azure-inspired shell and interaction rules, with Foundry
resource terminology and only controls backed by implemented endpoints.

The APIM stylesheet contains older rules followed by overrides; copying its
opening rules would restore the obsolete decorative design. These are the
effective tokens captured from `ui/src/styles.css`:

| Token | Light | Dark |
| --- | --- | --- |
| Background | `#f3f2f1` | `#111827` |
| Panel | `#ffffff` | `#1f2937` |
| Text | `#242424` | `#f3f4f6` |
| Muted text | `#605e5c` | `#c4cbd5` |
| Border | `#d2d0ce` | `#4b5563` |
| Accent | `#0078d4` | `#60a5fa` |
| Accent background | `#e5f1fb` | `#243b53` |

Use the system Segoe UI stack, compact 13px detail text, monospace request
and response editors, flat panes, 2px corners, and a compact blue masthead.
Resource names and paths wrap rather than disappearing behind ellipses.
Move navigation above detail panes on narrow screens. Keep theme colors in
tokens so inputs, focus indicators, and errors remain readable in both themes.

A suitable Foundry workflow is deployment navigation plus settings inspection
and request testing; separate panels can expose cache statistics/flush,
safety diagnostics, and existing blocklist operations. Deployment settings
are currently read-only. Present latency as simulated, embeddings as lexical,
and safety verdicts as simulation. Do not show agent hosting, Azure account
administration, or deployment editing as usable features.

Carry these verified APIM interaction lessons forward:

- Put connection settings in a collapsible toolbar and show a focused welcome
  view while disconnected. Distinguish backend inference and admin credentials.
  Mask credentials, retain them only in memory, and require re-entry after reload
  or authentication failure; only persist non-secret connection preferences.
- Guard dirty drafts when changing resources or leaving the page. Preserve
  unedited fields when saving a resource. Distinguish Create from Save actions.
- Keep loading/connection guards explicit. Ensure grid/display CSS respects
  `[hidden]`; APIM browser verification caught a disconnected workspace leak.
- Build request forms from actual metadata and show status, headers, body,
  streamed events and diagnostics together. Keep raw payload inspection available.
- Verify the built UI in a real browser, including disconnected, narrow-screen,
  dark-theme, expired-key, dirty-draft and destructive-action states. A build
  alone does not verify the workflow.

## Performance lessons without inherited speedup claims

Use the extreme-software-optimization loop: baseline, profile, golden outputs,
one scored lever, behavior proof, then fresh measurement. APIM's routing and
expression gains identify useful methodology, not Foundry hotspots or expected
speedups. Separate simulated latency from compute latency and cold misses from
warm hits; report startup, request-loop time, throughput, tail latency and
memory under the actual workload.

Cache keys must include every dependency, including validation context and
live policy/configuration state. Cache immutable pure results or make ownership
explicit; cap retained entries and test eviction and next-request changes.
Preserve cache selection order, ties, TTL, threshold edges, token usage, safety
checks, stream framing, and side effects. Freeze time/IDs where needed for
goldens. Run the baseline with its own helpers and verify imported source paths:
an oracle sharing changed helpers, or an editable install loading the candidate
twice, can incorrectly prove equivalence. Exclude both harness and process
warmups deliberately, and use fresh result files per condition.

## Sources and deferred work

Source checkout: `../apim-simulator` at `3adc88e`, inspected read-only. Relevant evidence:

- `docs/portal-ui-pass.md`, `docs/OPERATOR-CONSOLE.md`, `ui/src/styles.css`:
  final visual shell, credential handling, authoring and browser regressions.
- `docs/LOCAL-LIFECYCLE.md`: separate fixture/runtime state, reuse and reset
  behavior, network expectations.
- `docs/GATEWAY-OPTIMIZATION-LEARNINGS.md`, `docs/GATEWAY-PERFORMANCE.md`,
  `docs/GATEWAY-PERFORMANCE-FOLLOWUP.md`: cache dependency/ownership boundaries,
  differential-oracle traps and benchmark qualifications.

Current Foundry mapping is grounded in `app/main.py`, `app/state.py`,
`app/routes/management.py`, `compose.yml`, `Makefile`, and the integration guide.
Keep this document self-contained; the sibling checkout is evidence, not a
runtime dependency. Browser UI, persistent blocklist/Responses storage,
shared worker state and deployment editing require separately scoped
implementation and contracts. APIM's deferred semantic-cache and content-safety
policies remain deferred until gateway implementations are explicitly verified;
service-side endpoints do not implement those gateway policies by themselves.
