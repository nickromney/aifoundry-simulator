---
name: verify-aifoundry-simulator
description: Bring up an isolated AI Foundry simulator container (compose project aifoundry-simulator-verify, loopback-only on 127.0.0.1:8120), send recorded chat/embedding/content-safety requests, run the repo's smoke scripts and the foundrysim CLI. Use when you need to prove a wire-shape, semantic-cache, content-safety or management change against the running container.
---

# Verify the AI Foundry simulator

The user surface is HTTP: Azure-shaped `/openai/deployments/<name>/...`
inference, `/contentsafety/text:*`, and admin-gated `/foundry/...`
management. The `foundrysim` CLI is a thin client over the same endpoints.
Completions are deterministic echoes. No model runs.

Helper: `skills/verify-aifoundry-simulator/scripts/control.sh`.

This skill is built on verify-kit. The shared library
(`scripts/verify-lib.sh`), the contract tests (`tests/contract.bats`) and the
make targets (`verify.mk`) are vendored from
`harnesses-private/nickromney/verify-kit`, pinned by `verify-kit.lock`. Do not
edit the vendored files. Only `scripts/control.sh` and `tests/aifoundry-simulator.bats`
are specific to this app. Check for drift with
`harnesses-private/nickromney/verify-kit/bin/vendor.sh --check skills/verify-aifoundry-simulator`.

## Quick path

```bash
make verify            # full cycle: launch, doctor, drive, smoke, cleanup; ends with VERIFY PASS
make verify-dry-run    # print the plan, change nothing
make verify-test       # shellcheck on the scripts and BATS files, then the contract and app tests
```

`make verify` runs `control.sh --execute verify`. It asserts every step and
exits non-zero, with `VERIFY FAIL` and no `VERIFY PASS`, on the first failed
check. Cleanup runs either way, and evidence is kept.

`make verify-test` runs the contract suite (`tests/contract.bats`, 18 cases:
interface, Bash 3.2, previews, fail-fast writes, shellcheck) and the app suite
(`tests/aifoundry-simulator.bats`, 10 cases: loopback and ownership checks,
refusals, and the "docker never called" checks, using a fake docker).

## Interface

```bash
C() { /bin/bash skills/verify-aifoundry-simulator/scripts/control.sh --execute "$@"; }
```

`control.sh [--help] [--dry-run|--execute] <command> [args]`

- `--help` prints usage and exits 0.
- Without `--execute`, the script only prints the plan (`INFO dry-run: ...`)
  and writes nothing. A bare call prints usage plus the plan.
- `--dry-run` is the same as omitting `--execute`, and wins over it.
- An unknown option or command exits 2.
- `control.sh --list-commands` prints one sample invocation per line.

Commands: `verify`, `launch`, `doctor`, `request`, `cli`, `smoke`, `cleanup`.

Environment: `VERIFY_RUN_ID` (the run to act on), `VERIFY_PORT` (default 8120;
use 8121 if 8120 is taken), `VERIFY_BASE_DIR` (state, default
`.run/verify`) and `VERIFY_EVIDENCE_ROOT` (evidence, default `.run/verify`).
`control.sh --help` lists these.

## Launch

```bash
C launch          # VERIFY_PORT=8121 C launch if 8120 is taken
```

`launch` runs `docker compose -p aifoundry-simulator-verify up --build
--detach --wait` with `FOUNDRY_PORT=8120` and `FOUNDRY_BIND_ADDRESS=127.0.0.1`.
That keeps the user's `make up` project and port 8020 untouched. compose.yml
binds `${FOUNDRY_BIND_ADDRESS:-127.0.0.1}` (since 2026-10-10; before that it
bound every interface). The harness pins loopback anyway, so an exported
override can't widen it. It takes about 15 s warm and uses one container.

It refuses if the project is already running or the port is in use.

## Doctor

```bash
C doctor
```

Read-only. It refuses when this run did not launch (`FAIL: this run did not
launch`) before it calls Docker. Otherwise it checks that our container
publishes the port, only on 127.0.0.1, and that `/foundry/health` returns
`{"status":"ok",…}`. It also prints the launch revision next to the current
HEAD.

## Drive

```bash
P='/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21'
K='api-key: local-foundry-key-alpha'
C cli cache-flush
C request POST "$P" "$K" --json '{"messages":[{"role":"user","content":"What is the capital of France?"}]}'   # x-semantic-cache: miss
C request POST "$P" "$K" --json '{"messages":[{"role":"user","content":"what is the capital of france"}]}'   # hit, score < 0.1
C cli cache-stats   # hits 1, misses 1 on a freshly launched container
C smoke             # scripts/smoke_foundry.py, smoke_semantic_cache.py, smoke_content_safety.py
```

`cli` and `smoke` exit with the status of the CLI or of the smoke scripts.
`request` records any HTTP status, so check the status in the evidence.

The keys are the documented demo values: `local-foundry-key-alpha` for the
API and `local-foundry-admin-key` for management, which `cli` passes for you.

## Evidence

Everything goes under `.run/verify/<run>/evidence/`, which is git-ignored:

- `revision.txt` and `launch.log`.
- `compose.log` and `down.log`, written at cleanup.
- `<HHMMSS-NNN>-request/`: `request.txt`, `status.txt`, `response-headers.txt`, `response-body.txt`.
- `<HHMMSS-NNN>-cli/`: `command.txt`, `stdout.txt`, `stderr.txt` and `exit-code.txt`.
- `<HHMMSS-NNN>-smoke/`: one transcript per script, `<script>.exit-code.txt` per script, plus `exit-code.txt` for the run.

Proof standards:

- Cache claims need the miss/hit header pair and `cache-stats` counters
  that agree.
- Safety claims need a request that should be blocked (400
  `content_filter`) and one that should pass.
- Note the simulated latency (400 ms on `gpt-demo`) when reporting timings.

## Cleanup

```bash
C cleanup
```

Saves the compose logs, then runs `docker compose -p
aifoundry-simulator-verify down --remove-orphans`. It only does this when the
run launched. Evidence stays.

## Gotchas

- Runtime state (cache, blocklist edits, counters) is per process. Flush the
  cache first, or relaunch, for clean numbers. `cache-flush` clears entries
  but not the hit and miss counters.
- `make smoke` targets port 8020 by default. Use `C smoke`, which points
  the scripts at this instance.
- The BATS tests never build, launch or start containers. Their refusal
  cases put a fake `docker` first on PATH and assert it was never called.
