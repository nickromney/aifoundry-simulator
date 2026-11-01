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

@test "a web skill rejects invalid execute scope before starting any application" {
  if ! web_skill; then skip "not a web skill"; fi
  VERIFY_BROWSERS=opera run_control --execute verify
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown browser 'opera'"* ]]
  [[ "$output" != *"VERIFY PASS"* ]]
  assert_no_writes
}

@test "shellcheck is clean for control.sh, the library and every BATS file" {
  if ! command -v shellcheck >/dev/null; then skip "shellcheck not installed"; fi
  run shellcheck -x -P "$SKILL_DIR/scripts" "$SCRIPT" "$LIB" "$SKILL_DIR"/tests/*.bats
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "source digest tolerates an unavailable caller locale without changing it" {
  local fixture clean bad
  fixture="$BATS_TEST_TMPDIR/source-repo"
  clean="$BATS_TEST_TMPDIR/clean.digest"
  bad="$BATS_TEST_TMPDIR/bad.digest"
  git init -q "$fixture"
  git -C "$fixture" -c user.name=Fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -q --allow-empty -m fixture
  printf 'untracked source content\n' > "$fixture/untracked.txt"
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
  run env LC_ALL=C "$BASH32" -c '
    set -euo pipefail
    source "$1"
    VK_ROOT="$2"
    vk_capture_source
    printf "%s\n" "$VK_SOURCE_DIGEST" > "$3"
  ' _ "$LIB" "$fixture" "$clean"
  [ "$status" -eq 0 ] || return 1
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
  run env LC_ALL=verify_kit_unavailable.UTF-8 "$BASH32" -c '
    set -euo pipefail
    source "$1"
    VK_ROOT="$2"
    vk_capture_source
    [ "$LC_ALL" = verify_kit_unavailable.UTF-8 ] || exit 1
    printf "%s\n" "$VK_SOURCE_DIGEST" > "$3"
  ' _ "$LIB" "$fixture" "$bad"
  [ "$status" -eq 0 ] || return 1
  if [[ "$output" == *"perl: warning:"* ]]; then
    printf "%s\n" "$output"
    return 1
  fi
  [[ "$(cat "$clean")" =~ ^[0-9a-f]{64}$ ]] || return 1
  cmp "$clean" "$bad"
}

source_capture_fixture() {
  SOURCE_REPO="$BATS_TEST_TMPDIR/source-capture"
  git init -q "$SOURCE_REPO"
  git -C "$SOURCE_REPO" -c user.name=Fixture -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -q --allow-empty -m fixture
}

@test "source capture refuses an unreadable file even when a later file is readable" {
  source_capture_fixture
  printf 'protected source\n' > "$SOURCE_REPO/a-protected"
  printf 'readable source\n' > "$SOURCE_REPO/z-readable"
  chmod 000 "$SOURCE_REPO/a-protected"
  if cat "$SOURCE_REPO/a-protected" >/dev/null 2>&1; then
    chmod 600 "$SOURCE_REPO/a-protected"
    skip "current user can bypass file permissions"
  fi
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
  run "$BASH32" -c '
    set -euo pipefail
    source "$1"
    VK_ROOT="$2"
    if vk_capture_source; then exit 99; fi
    [ "$VK_SOURCE_DIGEST" = unavailable ]
  ' _ "$LIB" "$SOURCE_REPO"
  chmod 600 "$SOURCE_REPO/a-protected"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "source capture propagates Git symlink and final hash command failures" {
  source_capture_fixture
  printf 'source\n' > "$SOURCE_REPO/source.txt"
  ln -s source.txt "$SOURCE_REPO/link"
  local bin="$BATS_TEST_TMPDIR/failing-source-tools" stage
  mkdir -p "$bin"
  export SOURCE_REAL_GIT SOURCE_REAL_READLINK SOURCE_REAL_SHASUM
  SOURCE_REAL_GIT="$(command -v git)"
  SOURCE_REAL_READLINK="$(command -v readlink)"
  SOURCE_REAL_SHASUM="$(command -v shasum)"
  cat > "$bin/git" <<'SH'
#!/bin/bash
case "${SOURCE_FAIL_STAGE}:$3" in git-diff:diff|git-list:ls-files) exit 8 ;; esac
exec "$SOURCE_REAL_GIT" "$@"
SH
  cat > "$bin/readlink" <<'SH'
#!/bin/bash
if [ "$SOURCE_FAIL_STAGE" = readlink ]; then exit 8; fi
exec "$SOURCE_REAL_READLINK" "$@"
SH
  cat > "$bin/shasum" <<'SH'
#!/bin/bash
if [ "$SOURCE_FAIL_STAGE" = final-hash ] && [ "$#" -eq 2 ]; then exit 8; fi
exec "$SOURCE_REAL_SHASUM" "$@"
SH
  chmod +x "$bin"/*
  for stage in git-diff git-list readlink final-hash; do
    # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
    run env SOURCE_FAIL_STAGE="$stage" PATH="$bin:$PATH" "$BASH32" -c '
      set -euo pipefail
      source "$1"
      VK_ROOT="$2"
      if vk_capture_source; then exit 99; fi
      [ "$VK_SOURCE_DIGEST" = unavailable ]
    ' _ "$LIB" "$SOURCE_REPO"
    [ "$status" -eq 0 ] || { echo "$stage: $output"; return 1; }
  done
}

@test "source capture refuses unreadable directories even when Git exits zero" {
  source_capture_fixture
  mkdir "$SOURCE_REPO/protected"
  printf 'hidden source\n' > "$SOURCE_REPO/protected/source.txt"
  chmod 000 "$SOURCE_REPO/protected"
  if ls "$SOURCE_REPO/protected" >/dev/null 2>&1; then
    chmod 700 "$SOURCE_REPO/protected"
    skip "current user can bypass directory permissions"
  fi
  local git_status capture_status diagnostics
  run env LC_ALL=C git -C "$SOURCE_REPO" ls-files --others --exclude-standard -z
  git_status="$status"
  diagnostics="$output"
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
  run "$BASH32" -c '
    set -euo pipefail
    source "$1"
    VK_ROOT="$2"
    if vk_capture_source; then exit 99; fi
    [ "$VK_SOURCE_DIGEST" = unavailable ]
  ' _ "$LIB" "$SOURCE_REPO"
  capture_status="$status"
  chmod 700 "$SOURCE_REPO/protected"
  [ "$git_status" -eq 0 ]
  [[ "$diagnostics" == *"warning:"* ]]
  [ "$capture_status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "source capture rejects successful Git diagnostics and removes private temporary files" {
  source_capture_fixture
  printf 'source\n' > "$SOURCE_REPO/source.txt"
  local bin="$BATS_TEST_TMPDIR/warning-source-tools" temporary="$BATS_TEST_TMPDIR/source-temporary" stage
  mkdir -m 700 "$bin" "$temporary"
  printf 'caller-owned temporary file\n' > "$temporary/caller-sentinel"
  export SOURCE_WARNING_REAL_GIT
  SOURCE_WARNING_REAL_GIT="$(command -v git)"
  cat > "$bin/git" <<'SH'
#!/bin/bash
"$SOURCE_WARNING_REAL_GIT" "$@" || exit $?
case "${SOURCE_WARN_STAGE}:$3" in
  git-diff:diff|git-list:ls-files) printf 'warning: source enumeration was incomplete\n' >&2 ;;
esac
SH
  chmod +x "$bin/git"
  for stage in git-diff git-list; do
    # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
    run env SOURCE_WARN_STAGE="$stage" TMPDIR="$temporary" PATH="$bin:$PATH" "$BASH32" -c '
      set -euo pipefail
      source "$1"
      VK_ROOT="$2"
      trap '"'"'printf "caller trap preserved\n" > "$3"'"'"' EXIT
      original_trap="$(trap -p EXIT)"
      if vk_capture_source; then exit 99; fi
      [ "$VK_SOURCE_DIGEST" = unavailable ]
      [ "$(trap -p EXIT)" = "$original_trap" ]
    ' _ "$LIB" "$SOURCE_REPO" "$temporary/caller-trap"
    [ "$status" -eq 0 ] || { echo "$stage: $output"; return 1; }
    [[ "$output" == *"warning: source enumeration was incomplete"* ]]
    grep -Fx 'caller trap preserved' "$temporary/caller-trap"
    rm "$temporary/caller-trap"
    [ -z "$(find "$temporary" -name 'verify-kit-source.*' -print -quit)" ]
    grep -Fx 'caller-owned temporary file' "$temporary/caller-sentinel"
  done
}


@test "source capture refuses a temporary directory inside its source repository" {
  source_capture_fixture
  printf 'source\n' > "$SOURCE_REPO/source.txt"
  # shellcheck disable=SC2016 # expanded by the Bash 3.2 child
  run env TMPDIR="$SOURCE_REPO" "$BASH32" -c '
    set -euo pipefail
    source "$1"
    VK_ROOT="$2"
    if vk_capture_source; then exit 99; fi
    [ "$VK_SOURCE_DIGEST" = unavailable ]
  ' _ "$LIB" "$SOURCE_REPO"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
  [[ "$output" == *"requires TMPDIR outside the source repository"* ]]
  [ -z "$(find "$SOURCE_REPO" -name 'verify-kit-source.*' -print -quit)" ]
}
