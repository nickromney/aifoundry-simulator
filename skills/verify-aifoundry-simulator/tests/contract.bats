#!/usr/bin/env bats
# contract.bats: the verify-kit contract every verification skill must meet.
#
# Vendored into skills/verify-<app>/tests/contract.bats. Do not edit a vendored copy;
# change the origin (harnesses-private/nickromney/verify-kit) and re-vendor with bin/vendor.sh.
# Each case runs the skill's control.sh under macOS /bin/bash (3.2). The cases never build,
# launch, start servers or containers, open browsers, or touch real state. App-specific
# behaviour (refusals, allowlists) belongs in a sibling tests/<app>.bats.

setup() {
  SKILL_DIR="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  SCRIPT="$SKILL_DIR/scripts/control.sh"
  LIB="$SKILL_DIR/scripts/verify-lib.sh"
  BASH32="${VERIFY_TEST_BASH:-/bin/bash}"
  export VERIFY_BASE_DIR="$BATS_TEST_TMPDIR/base"
  export VERIFY_EVIDENCE_ROOT="$BATS_TEST_TMPDIR/evidence"
  export VERIFY_RUN_ID="contract-run"
}

run_control() { run "$BASH32" "$SCRIPT" "$@"; }

assert_no_writes() {
  if [ -e "$VERIFY_BASE_DIR" ]; then echo "wrote $VERIFY_BASE_DIR"; return 1; fi
  if [ -e "$VERIFY_EVIDENCE_ROOT" ]; then echo "wrote $VERIFY_EVIDENCE_ROOT"; return 1; fi
}

# Each sample invocation from `control.sh --list-commands`, one per line.
sample_commands() { "$BASH32" "$SCRIPT" --list-commands; }

@test "test interpreter is Bash 3.2 on macOS" {
  if [ "$(uname)" != Darwin ]; then skip "not macOS"; fi
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child, not by the test shell
  run "$BASH32" -c 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'
  [ "$status" -eq 0 ]
  [ "$output" = "3.2" ]
}

@test "control.sh and the vendored library parse under Bash 3.2" {
  run "$BASH32" -n "$SCRIPT"
  [ "$status" -eq 0 ]
  run "$BASH32" -n "$LIB"
  [ "$status" -eq 0 ]
}

@test "vendored kit files match verify-kit.lock" {
  [ -f "$SKILL_DIR/verify-kit.lock" ]
  while read -r kind path hash; do
    [ "$kind" = sha256 ] || continue
    actual="$(shasum -a 256 "$SKILL_DIR/$path" | awk '{print $1}')"
    [ "$actual" = "$hash" ] || { echo "$path drifted: $actual != $hash"; return 1; }
  done < "$SKILL_DIR/verify-kit.lock"
}

@test "no Bash 4-only constructs in control.sh or the library" {
  run grep -nE '(mapfile|readarray|declare -A|declare -n|local -n|wait -n|coproc|&>>|\$\{[A-Za-z_][A-Za-z_0-9]*(,,|\^\^))' "$SCRIPT" "$LIB"
  [ "$status" -eq 1 ] || { echo "$output"; return 1; }
}

@test "--help prints usage with the standard flags, verify and environment" {
  run_control --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: control.sh [--help] [--dry-run|--execute] <command>"* ]]
  [[ "$output" == *"--dry-run"* ]]
  [[ "$output" == *"--execute"* ]]
  [[ "$output" == *"  verify "* ]]
  [[ "$output" == *"VERIFY_BASE_DIR"* ]]
  [[ "$output" == *"verify-kit "* ]]
  assert_no_writes
}

@test "--help wins after --execute" {
  run_control --execute --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: control.sh"* ]]
  assert_no_writes
}

@test "bare invocation prints usage plus a dry-run line and writes nothing" {
  run_control
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: control.sh"* ]]
  [[ "$output" == *"INFO dry-run: "* ]]
  assert_no_writes
}

@test "the skill lists sample commands, including verify" {
  run sample_commands
  [ "$status" -eq 0 ]
  [ -n "$output" ]
  [[ "$output" == *verify* ]]
}

@test "every sample command previews without --execute and writes nothing" {
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    eval "set -- $line"
    run "$BASH32" "$SCRIPT" "$@"
    [ "$status" -eq 0 ] || { echo "'$line' -> $status: $output"; return 1; }
    [[ "$output" == *"INFO dry-run: "* ]] || { echo "'$line': $output"; return 1; }
  done < <(sample_commands)
  assert_no_writes
}

@test "every sample command under --dry-run prints only plan lines and writes nothing" {
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    eval "set -- $line"
    run "$BASH32" "$SCRIPT" --dry-run "$@"
    [ "$status" -eq 0 ] || { echo "'$line' -> $status: $output"; return 1; }
    [[ "$output" == "INFO dry-run: "* ]] || { echo "'$line': $output"; return 1; }
    [[ "$output" != *"Usage:"* ]] || { echo "'$line' printed usage under --dry-run"; return 1; }
  done < <(sample_commands)
  assert_no_writes
}

@test "--dry-run --execute stays a dry run" {
  run_control --dry-run --execute verify
  [ "$status" -eq 0 ]
  [[ "$output" == "INFO dry-run: "* ]]
  assert_no_writes
}

@test "unknown option exits 2" {
  run_control --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option --bogus"* ]]
}

@test "unknown command exits 2 in preview and execute modes" {
  run_control --dry-run frobnicate
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command 'frobnicate'"* ]]
  run_control --execute frobnicate
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command 'frobnicate'"* ]]
  assert_no_writes
}

@test "--execute without a command exits 2" {
  run_control --execute
  [ "$status" -eq 2 ]
  assert_no_writes
}

@test "doctor fails cleanly when nothing was launched" {
  run_control --execute doctor
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL:"* ]]
}

@test "cleanup of a never-launched run succeeds and leaves other runs alone" {
  mkdir -p "$VERIFY_BASE_DIR/other-run"
  echo other-run > "$VERIFY_BASE_DIR/current"
  run_control --execute cleanup
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
  [ -d "$VERIFY_BASE_DIR/other-run" ]
  [ "$(cat "$VERIFY_BASE_DIR/current")" = other-run ]
}

@test "verify fails fast without VERIFY PASS when state cannot be written" {
  export VERIFY_BASE_DIR="/dev/null/not-a-dir"
  run_control --execute verify
  [ "$status" -ne 0 ]
  [[ "$output" != *"VERIFY PASS"* ]]
  [[ "$output" == *"FAIL: cannot write"* ]]
}

@test "control.sh is shellcheck clean without -x, as repo pre-commit hooks run it" {
  if ! command -v shellcheck >/dev/null; then skip "shellcheck not installed"; fi
  run shellcheck "$SCRIPT"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "verify rejects extra arguments in both modes" {
  run_control --dry-run verify extra
  [ "$status" -eq 2 ]
  run_control --execute verify extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"verify takes no arguments"* ]]
  assert_no_writes
}

# Web skills (VK_BROWSER_SKILL=1) only; other skills print nothing for --list-browsers.
web_skill() { [ -n "$("$BASH32" "$SCRIPT" --list-browsers)" ]; }

@test "a web skill drives chromium, firefox and webkit by default and documents VERIFY_BROWSERS" {
  if ! web_skill; then skip "not a web skill"; fi
  run "$BASH32" "$SCRIPT" --list-browsers
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'chromium\nfirefox\nwebkit')" ]
  run_control --help
  [[ "$output" == *"VERIFY_BROWSERS"* ]]
}

@test "a web skill narrows to VERIFY_BROWSERS and rejects unknown or empty values" {
  if ! web_skill; then skip "not a web skill"; fi
  VERIFY_BROWSERS="webkit,firefox webkit" run "$BASH32" "$SCRIPT" --list-browsers
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'webkit\nfirefox')" ]
  VERIFY_BROWSERS="chromium opera" run "$BASH32" "$SCRIPT" --list-browsers
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown browser 'opera'"* ]]
  VERIFY_BROWSERS=" , " run "$BASH32" "$SCRIPT" --list-browsers
  [ "$status" -eq 2 ]
}

@test "a web skill's verify plan names every browser it will drive" {
  if ! web_skill; then skip "not a web skill"; fi
  run_control --dry-run verify
  [ "$status" -eq 0 ]
  for b in chromium firefox webkit; do [[ "$output" == *"$b"* ]] || { echo "plan lacks $b: $output"; return 1; }; done
  VERIFY_BROWSERS=firefox run "$BASH32" "$SCRIPT" --dry-run verify
  [[ "$output" == *firefox* ]]
  [[ "$output" != *webkit* ]]
  assert_no_writes
}

@test "shellcheck is clean for control.sh, the library and every BATS file" {
  if ! command -v shellcheck >/dev/null; then skip "shellcheck not installed"; fi
  run shellcheck -x -P "$SKILL_DIR/scripts" "$SCRIPT" "$LIB" "$SKILL_DIR"/tests/*.bats
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}
