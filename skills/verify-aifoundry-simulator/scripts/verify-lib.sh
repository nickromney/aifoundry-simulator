# shellcheck shell=bash
# verify-lib.sh: the shared harness for project verification skills (verify-kit).
#
# Vendored into skills/verify-<app>/scripts/verify-lib.sh. Do not edit a vendored copy;
# change the origin (harnesses-private/nickromney/verify-kit) and re-vendor with bin/vendor.sh.
# Bash 3.2 compatible (macOS /bin/bash), BSD userland only.
#
# A skill's scripts/control.sh sets configuration, sources this file, defines hooks, then calls
# `vk_main "$@"`:
#
#   VK_APP                    short app name, used in default paths (required)
#   VK_DESCRIPTION            one line for --help (required)
#   VK_DEFAULT_BASE_DIR       state/run root   (default: $TMPDIR/verify-$VK_APP)
#   VK_DEFAULT_EVIDENCE_ROOT  evidence root    (default: the base dir)
#
#   vk_commands_help          print the skill's command lines for --help (two-space indent)
#   vk_sample_commands        print one sample invocation per line (the contract tests dry-run each)
#   vk_paths                  derive the skill's paths (must set EVIDENCE) from VK_BASE_DIR,
#                             VK_EVIDENCE_ROOT and VK_RUN_ID; must not write anything
#   vk_plan <cmd> [args]      print what <cmd> would do with vk_plan_line
#   vk_dispatch <cmd> [args]  run <cmd>
#                             (both end their case with `*) vk_unknown_command ;;`, which exits 2)
#   cmd_launch, cmd_doctor, cmd_cleanup, vk_verify_recipes
#   vk_env_help               optional: extra environment lines for --help
#   VK_BROWSER_SKILL=1        optional: the skill drives a web UI, so its recipes run once per
#                             browser from vk_browsers (VERIFY_BROWSERS, default all three engines)
#
# Every control.sh carries the same two shellcheck directives (spelled out in README.md): a
# file-level disable of SC2034, since VK_* settings are read here rather than in control.sh,
# and a source=verify-lib.sh directive that also disables SC1091 on the sourcing line. Repo
# hooks that run shellcheck without -x then stay clean; the contract tests check both ways.
#
# Interface every skill gets: control.sh [--help] [--dry-run|--execute] <command> [args]
# Nothing runs without --execute. A bare call, or a call without --execute, prints the plan.
# `verify` = launch, doctor, recipes, cleanup (cleanup also on failure), then VERIFY PASS.

# Variables such as EVIDENCE are assigned by the skill's vk_paths hook, not here.
# shellcheck disable=SC2154

VK_KIT_VERSION="1.2.1"
VK_SCRIPT_NAME="control.sh"
VK_VERIFY_ACTIVE=0

vk_say() { printf '%s\n' "$*"; }
vk_err() { printf '%s: %s\n' "$VK_SCRIPT_NAME" "$*" >&2; }
vk_plan_line() { printf 'INFO dry-run: %s\n' "$*"; }
vk_fail() { vk_say "FAIL: $*"; return 1; }
vk_unknown_command() { vk_err "unknown command '$VK_CMD'"; return 2; }

vk_usage() {
  cat <<EOF
Usage: $VK_SCRIPT_NAME [--help] [--dry-run|--execute] <command> [args]

$VK_DESCRIPTION
Nothing runs without --execute; a bare call or --dry-run prints the plan instead.

Commands:
  verify                 full proven cycle: launch, doctor, recipes, cleanup (cleanup also on failure)
EOF
  vk_commands_help
  cat <<EOF

Options:
  -h, --help             show this help
  --dry-run              print what the command would do, change nothing
  --execute              run the command

Environment:
  VERIFY_RUN_ID          run to act on (default: the current run, or a new one for launch/verify)
  VERIFY_BASE_DIR        state root (default: $VK_DEFAULT_BASE_DIR)
  VERIFY_EVIDENCE_ROOT   evidence root (default: $VK_DEFAULT_EVIDENCE_ROOT)
EOF
  if [ "${VK_BROWSER_SKILL:-0}" = 1 ]; then
    printf '%s\n' "  VERIFY_BROWSERS        browsers to drive, space or comma separated (default: $VK_BROWSERS_ALL)"
  fi
  if declare -F vk_env_help >/dev/null; then vk_env_help; fi
  printf '\nverify-kit %s\n' "$VK_KIT_VERSION"
}

# Resolve the repository root, the base/evidence roots and the run id. Reads only.
vk_resolve() {
  VK_SCRIPT_DIR="$(cd "$(dirname "$VK_SCRIPT")" && pwd)"
  VK_SKILL_DIR="$(cd "$VK_SCRIPT_DIR/.." && pwd)"
  VK_ROOT="$(git -C "$VK_SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$VK_SKILL_DIR")"
  local tmp_root="${TMPDIR:-/tmp}"
  VK_DEFAULT_BASE_DIR="${VK_DEFAULT_BASE_DIR:-${tmp_root%/}/verify-$VK_APP}"
  VK_DEFAULT_EVIDENCE_ROOT="${VK_DEFAULT_EVIDENCE_ROOT:-$VK_DEFAULT_BASE_DIR}"
  VK_BASE_DIR="${VERIFY_BASE_DIR:-$VK_DEFAULT_BASE_DIR}"
  VK_EVIDENCE_ROOT="${VERIFY_EVIDENCE_ROOT:-$VK_DEFAULT_EVIDENCE_ROOT}"
  if [ -n "${VERIFY_RUN_ID:-}" ]; then
    VK_RUN_ID="$VERIFY_RUN_ID"
  elif [ "$VK_CMD" = launch ] || [ "$VK_CMD" = verify ] || [ ! -f "$VK_BASE_DIR/current" ]; then
    VK_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
  else
    VK_RUN_ID="$(cat "$VK_BASE_DIR/current")"
  fi
  case "$VK_RUN_ID" in
    ""|.|..|*[!A-Za-z0-9._-]*)
      vk_err "invalid run id; use only letters, digits, dot, underscore or hyphen"
      return 2
      ;;
  esac
}

vk_mark_current() { mkdir -p "$VK_BASE_DIR" && printf '%s\n' "$VK_RUN_ID" > "$VK_BASE_DIR/current"; }
# Clear the current-run pointer only if it names this run.
vk_clear_current() {
  if [ -f "$VK_BASE_DIR/current" ] && [ "$(cat "$VK_BASE_DIR/current")" = "$VK_RUN_ID" ]; then
    rm -f "$VK_BASE_DIR/current"
  fi
}
vk_record_revision() {
  { git -C "$VK_ROOT" rev-parse HEAD 2>/dev/null || echo "not-a-git-checkout"; git -C "$VK_ROOT" status --porcelain 2>/dev/null || true; } > "$1"
}

# HHMMSS-NNN: sortable and unique within EVIDENCE, even for several calls per second.
vk_stamp() {
  printf '%s-%03d' "$(date -u +%H%M%S)" "$(find "$EVIDENCE" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
}

# ---- browsers (web skills) -------------------------------------------------------------------
# A web skill proves its UI in every engine it ships to, not only Chromium. vk_browsers prints
# the browsers to drive, one per line: VERIFY_BROWSERS (space or comma separated), default all
# three Playwright engines, duplicates dropped. An unknown name or an empty list exits 2.
VK_BROWSERS_ALL="chromium firefox webkit"
vk_browsers() {
  local list b seen=" "
  list="$(printf '%s' "${VERIFY_BROWSERS-$VK_BROWSERS_ALL}" | tr ',' ' ')"
  for b in $list; do
    case " $VK_BROWSERS_ALL " in
      *" $b "*) ;;
      *) vk_err "unknown browser '$b' in VERIFY_BROWSERS (use: $VK_BROWSERS_ALL)"; return 2 ;;
    esac
    case "$seen" in *" $b "*) continue ;; esac
    seen="$seen$b "
    printf '%s\n' "$b"
  done
  if [ "$seen" = " " ]; then vk_err "VERIFY_BROWSERS names no browser"; return 2; fi
}

# ---- assertions used by vk_verify_recipes ----------------------------------------------------
vk_pass() { vk_say "PASS: $*"; }
vk_expect_contains() {   # <file> <fixed text>
  if [ -f "$1" ] && grep -F -q -- "$2" "$1"; then vk_pass "$(basename "$1") contains '$2'"
  else vk_fail "$(basename "$1") lacks '$2'"; fi
}
vk_expect_not_contains() {   # <file> <fixed text>
  if [ -f "$1" ] && ! grep -F -q -- "$2" "$1"; then vk_pass "$(basename "$1") has no '$2'"
  else vk_fail "$(basename "$1") contains '$2' (or is missing)"; fi
}
vk_expect_equals() {   # <label> <actual> <expected>
  if [ "$2" = "$3" ]; then vk_pass "$1 = '$3'"; else vk_fail "$1 = '$2', expected '$3'"; fi
}
vk_expect_count() {   # <file> <fixed text> <expected line count>
  local n=0
  if [ -f "$1" ]; then n="$(grep -F -c -- "$2" "$1" || true)"; fi
  vk_expect_equals "$(basename "$1") lines containing '$2'" "$n" "$3"
}
vk_expect_line() {   # <file> <line prefix> <fixed text>: the first line starting with prefix contains text
  local line=""
  if [ -f "$1" ]; then line="$(grep -F -- "$2" "$1" | awk -v p="$2" 'index($0, p) == 1 { print; exit }')"; fi
  if [ -n "$line" ] && printf '%s\n' "$line" | grep -F -q -- "$3"; then vk_pass "$(basename "$1") line '$2...' contains '$3'"
  else vk_fail "$(basename "$1") has no line starting '$2' containing '$3'"; fi
}
vk_expect_file() {   # <path>
  if [ -e "$1" ]; then vk_pass "$1 exists"; else vk_fail "$1 is missing"; fi
}

# ---- processes and ports ---------------------------------------------------------------------
vk_port_listener() { lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1 || true; }
vk_port_busy() { [ -n "$(vk_port_listener "$1")" ]; }
# Start a command in its own process group so cleanup can stop exactly what this run started.
vk_spawn_group() {   # <pidfile> <logfile> <command...>
  local pidfile="$1" logfile="$2"
  shift 2
  nohup perl -e 'setpgrp(0,0); exec @ARGV' "$@" > "$logfile" 2>&1 < /dev/null &
  printf '%s\n' "$!" > "$pidfile"
}
vk_group_alive() {   # <pidfile>
  local g
  g="$(cat "$1" 2>/dev/null || true)"
  [ -n "$g" ] && { kill -0 "$g" 2>/dev/null || kill -0 -- "-$g" 2>/dev/null; }
}
vk_listener_in_group() {   # <port> <pidfile>
  local l g
  l="$(vk_port_listener "$1")"
  g="$(cat "$2" 2>/dev/null || true)"
  [ -n "$l" ] && [ -n "$g" ] && [ "$(ps -o pgid= -p "$l" | tr -d ' ')" = "$g" ]
}
vk_stop_group() {   # <pidfile>
  local g i=0
  g="$(cat "$1" 2>/dev/null || true)"
  [ -n "$g" ] || return 0
  kill -TERM -- "-$g" 2>/dev/null || kill -TERM "$g" 2>/dev/null || true
  while vk_group_alive "$1" && [ "$i" -lt 20 ]; do sleep 0.25; i=$((i + 1)); done
  if vk_group_alive "$1"; then kill -KILL -- "-$g" 2>/dev/null || true; fi
}
# Poll a command once a second until it succeeds or the timeout passes.
vk_wait_for() {   # <seconds> <command...>
  local limit="$1" i=0
  shift
  while [ "$i" -lt "$limit" ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1; i=$((i + 1))
  done
  return 1
}

# Like vk_wait_for, but give up at once if the process group in <pidfile> has exited.
vk_wait_for_alive() {   # <pidfile> <seconds> <command...>
  local pidfile="$1" limit="$2" i=0
  shift 2
  while [ "$i" -lt "$limit" ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    vk_group_alive "$pidfile" || return 2
    sleep 1; i=$((i + 1))
  done
  return 1
}

# ---- the verify cycle ------------------------------------------------------------------------
vk_on_verify_exit() {
  local rc=$?
  trap - EXIT
  # Bash can leave $? as zero when nounset terminates during parameter expansion.
  # The active marker distinguishes an interrupted verify cycle from a completed one.
  if [ "$VK_VERIFY_ACTIVE" -eq 1 ] && [ "$rc" -eq 0 ]; then rc=1; fi
  cmd_cleanup >/dev/null 2>&1 || true
  if [ "$rc" -ne 0 ]; then vk_say "VERIFY FAIL run=$VK_RUN_ID exit=$rc evidence=${EVIDENCE:-unknown}" >&2; fi
  exit "$rc"
}
vk_run_verify() {
  # Fail fast, before any build or start, when the run cannot record state or evidence.
  if ! mkdir -p "$VK_BASE_DIR/$VK_RUN_ID" "$VK_EVIDENCE_ROOT" 2>/dev/null; then
    vk_fail "cannot write $VK_BASE_DIR/$VK_RUN_ID or $VK_EVIDENCE_ROOT"
    return 1
  fi
  VK_VERIFY_ACTIVE=1
  trap vk_on_verify_exit EXIT
  cmd_launch
  cmd_doctor
  vk_verify_recipes
  cmd_cleanup
  VK_VERIFY_ACTIVE=0
  trap - EXIT
  vk_say "VERIFY PASS run=$VK_RUN_ID evidence=$EVIDENCE"
}

# ---- entry point -----------------------------------------------------------------------------
vk_main() {
  VK_DRY_RUN=0
  VK_EXECUTE=0
  VK_CMD=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) VK_CMD=""; vk_resolve; vk_usage; return 0 ;;
      --dry-run) VK_DRY_RUN=1; shift ;;
      --execute) VK_EXECUTE=1; shift ;;
      --list-commands) vk_sample_commands; return 0 ;;
      --list-browsers) if [ "${VK_BROWSER_SKILL:-0}" = 1 ]; then vk_browsers; fi; return ;;
      --) shift; break ;;
      -*) vk_err "unknown option $1"; return 2 ;;
      *) break ;;
    esac
  done
  VK_CMD="${1:-}"
  if [ $# -gt 0 ]; then shift; fi
  vk_resolve
  if [ "${VK_BROWSER_SKILL:-0}" = 1 ]; then
    if vk_browsers >/dev/null; then :; else return $?; fi
  fi
  vk_paths

  if [ "$VK_EXECUTE" -ne 1 ] || [ "$VK_DRY_RUN" -eq 1 ]; then
    if [ "$VK_DRY_RUN" -ne 1 ]; then vk_usage; echo; fi
    if [ -z "$VK_CMD" ]; then
      vk_plan_line "would run one of the commands above (run=$VK_RUN_ID)"
      return 0
    fi
    if [ "$VK_CMD" = verify ] && [ $# -gt 0 ]; then vk_err "verify takes no arguments"; return 2; fi
    # Call vk_plan as a plain command: errexit is suspended inside functions run from `||` or
    # `if`, so a skill's argument check (return 2 from a helper) must abort the plan right here.
    vk_plan "$VK_CMD" ${@+"$@"}
    return 0
  fi

  case "$VK_CMD" in
    "") vk_err "--execute needs a command"; return 2 ;;
    verify)
      if [ $# -gt 0 ]; then vk_err "verify takes no arguments"; return 2; fi
      vk_run_verify
      ;;
    *)
      vk_dispatch "$VK_CMD" ${@+"$@"}
      ;;
  esac
}
