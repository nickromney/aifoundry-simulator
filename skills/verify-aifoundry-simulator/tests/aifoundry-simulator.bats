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

@test "shellcheck is clean for the app test file" {
  if ! command -v shellcheck >/dev/null; then skip "shellcheck not installed"; fi
  run shellcheck -x "$BATS_TEST_FILENAME"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}
