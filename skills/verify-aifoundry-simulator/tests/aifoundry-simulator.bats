#!/usr/bin/env bats
# App-specific tests for the AI Foundry simulator verification skill (the shared contract is in contract.bats).
# Runs under macOS /bin/bash 3.2. Docker is always a fake that logs its calls, so these cases never build,
# start containers or touch the real compose project. Refusals must leave the fake's call log empty.

setup() {
  SKILL_DIR="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  SCRIPT="$SKILL_DIR/scripts/control.sh"
  BASH32="${VERIFY_TEST_BASH:-/bin/bash}"
  export VERIFY_BASE_DIR="$BATS_TEST_TMPDIR/base"
  export VERIFY_EVIDENCE_ROOT="$BATS_TEST_TMPDIR/evidence"
  export VERIFY_RUN_ID="app-run"
}

run_control() { run "$BASH32" "$SCRIPT" "$@"; }

# A fake docker: logs every call, and answers `compose ls`, `ps` and `port` from fake-* files in the
# test temp dir (set_fake writes them). Anything else exits 99.
fake_docker() {
  mkdir -p "$BATS_TEST_TMPDIR/fakebin"
  cat > "$BATS_TEST_TMPDIR/fakebin/docker" <<'FAKE'
#!/bin/bash
echo "$*" >> "$BATS_TEST_TMPDIR/docker-calls.txt"
fake() { cat "$BATS_TEST_TMPDIR/fake-$1" 2>/dev/null || printf '%s\n' "$2"; }
case "$1 $2" in
  "compose ls") fake compose-ls '[]' ;;
  "ps --filter") fake owner '' ;;
  "port "*) fake binding '127.0.0.1:8120' ;;
  *) exit 99 ;;
esac
FAKE
  chmod +x "$BATS_TEST_TMPDIR/fakebin/docker"
  export PATH="$BATS_TEST_TMPDIR/fakebin:$PATH"
}
set_fake() { printf '%s\n' "$2" > "$BATS_TEST_TMPDIR/fake-$1"; }

assert_docker_not_called() {
  [ ! -e "$BATS_TEST_TMPDIR/docker-calls.txt" ] || { echo "docker was called: $(cat "$BATS_TEST_TMPDIR/docker-calls.txt")"; return 1; }
}
# assert_docker_log_lacks <ERE>: no logged docker call matches the pattern.
assert_docker_log_lacks() {
  if [ ! -e "$BATS_TEST_TMPDIR/docker-calls.txt" ]; then return 0; fi
  run grep -E -- "$1" "$BATS_TEST_TMPDIR/docker-calls.txt"
  [ "$status" -ne 0 ] || { echo "docker log has a call matching '$1': $output"; return 1; }
}
assert_no_evidence() {
  [ ! -e "$VERIFY_EVIDENCE_ROOT" ] || { echo "wrote $VERIFY_EVIDENCE_ROOT"; return 1; }
}
# Make this run look launched (state dir, port, revision) without building anything.
fake_launched_run() {
  mkdir -p "$VERIFY_BASE_DIR/$VERIFY_RUN_ID/state" "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence"
  echo 8120 > "$VERIFY_BASE_DIR/$VERIFY_RUN_ID/state/port"
  echo "deadbeef" > "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/revision.txt"
}

@test "doctor refuses when this run did not launch (no docker)" {
  fake_docker
  run_control --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: this run did not launch"* ]]
  assert_docker_not_called
}

@test "request, cli and smoke refuse when this run did not launch and record nothing" {
  fake_docker
  run_control --execute request POST /foundry/health
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: this run did not launch"* ]]
  run_control --execute cli cache-stats
  [ "$status" -eq 1 ]
  run_control --execute smoke
  [ "$status" -eq 1 ]
  assert_no_evidence
  assert_docker_not_called
}

@test "cleanup of a never-launched run never calls docker" {
  mkdir -p "$VERIFY_BASE_DIR/other-run"
  fake_docker
  run_control --execute cleanup
  [ "$status" -eq 0 ]
  [ -d "$VERIFY_BASE_DIR/other-run" ]
  assert_docker_not_called
}

@test "launch refuses when the compose project is already running and only lists projects" {
  fake_docker
  set_fake compose-ls '[{"Name":"aifoundry-simulator-verify","Status":"running(1)"}]'
  run_control --execute launch
  [ "$status" -eq 1 ]
  [[ "$output" == *"already running; run cleanup first"* ]]
  grep -q '^compose ls' "$BATS_TEST_TMPDIR/docker-calls.txt"
  assert_docker_log_lacks 'compose up'
}

@test "verify refuses to touch another run's stack: no logs or down when launch is refused" {
  fake_docker
  set_fake compose-ls '[{"Name":"aifoundry-simulator-verify","Status":"running(1)"}]'
  run_control --execute verify
  [ "$status" -ne 0 ]
  [[ "$output" == *"VERIFY FAIL"* ]]
  [[ "$output" != *"VERIFY PASS"* ]]
  assert_docker_log_lacks 'compose (up|down|logs)'
}

@test "doctor refuses when the port is published by a container other than ours" {
  fake_docker
  fake_launched_run
  set_fake owner "some-other-container"
  run_control --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: port 8120 published by 'some-other-container'"* ]]
  assert_docker_log_lacks '^port '
}

@test "doctor refuses when our container publishes beyond loopback" {
  fake_docker
  fake_launched_run
  set_fake owner "aifoundry-simulator-verify-aifoundry-simulator-1"
  set_fake binding "0.0.0.0:8120"
  run_control --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: published on 0.0.0.0:8120, not loopback"* ]]
}

@test "launch dry-run pins the loopback binding and the verification port" {
  run_control --dry-run launch
  [ "$status" -eq 0 ]
  [[ "$output" == *"FOUNDRY_BIND_ADDRESS=127.0.0.1 FOUNDRY_PORT=8120 docker compose -p aifoundry-simulator-verify up --build --detach --wait"* ]]
}

@test "verify dry-run names the cache, stats and smoke assertions" {
  run_control --dry-run verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"chat miss, paraphrase hit"* ]]
  [[ "$output" == *"hits 1, misses 1"* ]]
  [[ "$output" == *"smoke x3"* ]]
}

# Fake curl: answers /foundry/health (exit 22 when fake-health-down exists), writes the -D header file
# and -o body file, and prints the status for -w. Logs each call. Header/status come from fake-* files.
fake_curl() {
  mkdir -p "$BATS_TEST_TMPDIR/fakebin"
  cat > "$BATS_TEST_TMPDIR/fakebin/curl" <<'FAKE'
#!/bin/bash
echo "$*" >> "$BATS_TEST_TMPDIR/curl-calls.txt"
case "$*" in *foundry/health*) [ -e "$BATS_TEST_TMPDIR/fake-health-down" ] && exit 22 ;; esac
hdr=""; out=""; prev=""; wout=0
for a in "$@"; do
  case "$prev" in -D) hdr="$a" ;; -o) out="$a" ;; esac
  [ "$a" = -w ] && wout=1
  prev="$a"
done
body='{"status":"ok"}'
if [ -n "$hdr" ]; then
  if [ -e "$BATS_TEST_TMPDIR/fake-headers" ]; then cat "$BATS_TEST_TMPDIR/fake-headers"; else printf 'HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n'; fi > "$hdr"
fi
if [ -n "$out" ]; then printf '%s' "$body" > "$out"; fi
if [ "$wout" = 1 ]; then cat "$BATS_TEST_TMPDIR/fake-status" 2>/dev/null || printf '200'; fi
if [ "$wout" = 0 ] && [ -z "$out" ]; then printf '%s' "$body"; fi
exit 0
FAKE
  chmod +x "$BATS_TEST_TMPDIR/fakebin/curl"
  export PATH="$BATS_TEST_TMPDIR/fakebin:$PATH"
}

# Fake uv: logs its arguments; exits FAKE_UV_RC (default 0), or 5 when its arguments contain FAKE_UV_FAIL.
fake_uv() {
  mkdir -p "$BATS_TEST_TMPDIR/fakebin"
  cat > "$BATS_TEST_TMPDIR/fakebin/uv" <<'FAKE'
#!/bin/bash
echo "$*" >> "$BATS_TEST_TMPDIR/uv-calls.txt"
if [ -n "${FAKE_UV_FAIL:-}" ] && [[ "$*" == *"$FAKE_UV_FAIL"* ]]; then exit 5; fi
exit "${FAKE_UV_RC:-0}"
FAKE
  chmod +x "$BATS_TEST_TMPDIR/fakebin/uv"
  export PATH="$BATS_TEST_TMPDIR/fakebin:$PATH"
}

@test "doctor passes for our loopback container with a healthy /foundry/health" {
  fake_docker
  fake_curl
  fake_launched_run
  set_fake owner "aifoundry-simulator-verify-aifoundry-simulator-1"
  run_control --execute doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *'OK binding=127.0.0.1:8120 health={"status":"ok"}'* ]]
  [[ "$output" == *"revision deadbeef (now $(git -C "$BATS_TEST_DIRNAME" rev-parse HEAD))"* ]]
}

@test "doctor fails when /foundry/health does not answer" {
  fake_docker
  fake_curl
  fake_launched_run
  set_fake owner "aifoundry-simulator-verify-aifoundry-simulator-1"
  touch "$BATS_TEST_TMPDIR/fake-health-down"
  run_control --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: /foundry/health"* ]]
  [[ "$output" != *"OK binding="* ]]
}

# Equivalent mutants (accepted): `return 0` after vk_fail in cmd_doctor's guards is unreachable,
# because vk_fail returns 1 and set -e exits first. `grep ... && true` (cmd_request) and the
# `compose logs/down ... && true` and `rmdir ... && true` forms (cmd_cleanup) fail without errexit
# (the failing command is not the last one of the && list), matching the `|| true` originals.
@test "request with no extra headers records the status, the request line and the evidence" {
  fake_docker
  fake_curl
  fake_launched_run
  set_fake status 201
  run_control --execute request GET /foundry/health
  [ "$status" -eq 0 ]
  [[ "$output" == *"status=201"* ]]
  local dir
  dir="$(ls -d "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/"*-request)"
  [ "$(cat "$dir/status.txt")" = 201 ]
  [ "$(cat "$dir/request.txt")" = "GET http://127.0.0.1:8120/foundry/health" ]
  [[ "$output" == *"evidence: $dir"* ]]
}

@test "request passes each header and a --json body through to curl and the transcript" {
  fake_docker
  fake_curl
  fake_launched_run
  set_fake headers "$(printf 'HTTP/1.1 200 OK\r\nx-semantic-cache: miss\r\n')"
  run_control --execute request POST /v1/x --json '{"a":1}' "X-Test: one"
  [ "$status" -eq 0 ]
  [[ "$output" == *"x-semantic-cache: miss"* ]]
  local dir
  dir="$(ls -d "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/"*-request)"
  grep -q -- "--data {\"a\":1}" "$BATS_TEST_TMPDIR/curl-calls.txt"
  grep -q -- "-H X-Test: one" "$BATS_TEST_TMPDIR/curl-calls.txt"
  grep -qx 'body: {"a":1}' "$dir/request.txt"
  grep -qx 'X-Test: one' "$dir/request.txt"
}

@test "cli records and returns the foundrysim exit code and runs it against this instance" {
  fake_docker
  fake_uv
  fake_launched_run
  export FAKE_UV_RC=3
  run_control --execute cli cache-stats
  [ "$status" -eq 3 ]
  local dir
  dir="$(ls -d "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/"*-cli)"
  [ "$(cat "$dir/exit-code.txt")" = 3 ]
  grep -q -- 'foundrysim --base-url http://127.0.0.1:8120' "$BATS_TEST_TMPDIR/uv-calls.txt"
}

@test "smoke records each script's exit code and fails overall when one script fails" {
  fake_docker
  fake_uv
  fake_launched_run
  export FAKE_UV_FAIL=smoke_semantic_cache
  run_control --execute smoke
  [ "$status" -eq 1 ]
  local dir
  dir="$(ls -d "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/"*-smoke)"
  [ "$(cat "$dir/smoke_foundry.exit-code.txt")" = 0 ]
  [ "$(cat "$dir/smoke_semantic_cache.exit-code.txt")" = 5 ]
  [ "$(cat "$dir/smoke_content_safety.exit-code.txt")" = 0 ]
  [ "$(cat "$dir/exit-code.txt")" = 1 ]
}

@test "smoke passes overall when every script exits 0" {
  fake_docker
  fake_uv
  fake_launched_run
  run_control --execute smoke
  [ "$status" -eq 0 ]
  local dir
  dir="$(ls -d "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/"*-smoke)"
  [ "$(cat "$dir/exit-code.txt")" = 0 ]
  [ "$(wc -l < "$BATS_TEST_TMPDIR/uv-calls.txt" | tr -d ' ')" -eq 3 ]
}

@test "cleanup of this run stops its compose project, keeps the logs and removes the state" {
  fake_docker
  fake_launched_run
  run_control --execute cleanup
  [ "$status" -eq 0 ]
  grep -q '^compose -p aifoundry-simulator-verify logs --no-color$' "$BATS_TEST_TMPDIR/docker-calls.txt"
  grep -q '^compose -p aifoundry-simulator-verify down --remove-orphans$' "$BATS_TEST_TMPDIR/docker-calls.txt"
  [ ! -e "$VERIFY_BASE_DIR/$VERIFY_RUN_ID/state" ]
  [[ "$output" == *"stopped run=app-run project=aifoundry-simulator-verify"* ]]
}

@test "cleanup still succeeds when compose logs and down fail" {
  fake_docker
  fake_launched_run
  run_control --execute cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"stopped run=app-run"* ]]
  [ -f "$VERIFY_EVIDENCE_ROOT/$VERIFY_RUN_ID/evidence/down.log" ]
}

@test "shellcheck is clean for the app test file" {
  if ! command -v shellcheck >/dev/null; then skip "shellcheck not installed"; fi
  run shellcheck -x "$BATS_TEST_FILENAME"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}
