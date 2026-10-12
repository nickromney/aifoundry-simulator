#!/usr/bin/env bash
# Verification harness for the AI Foundry simulator: own compose project, loopback-only port.
# Built on verify-kit (scripts/verify-lib.sh). Bash 3.2 compatible (macOS /bin/bash). Run with --help for usage.
# shellcheck disable=SC2034 # VK_* settings are read by the sourced verify-lib.sh
set -euo pipefail

VK_SCRIPT="$0"
VK_APP="aifoundry-simulator"
VK_DESCRIPTION="Verification harness for the AI Foundry simulator: own compose project on 127.0.0.1 only."
VK_DEFAULT_BASE_DIR="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.run/verify"   # .run/ is git-ignored
VK_DEFAULT_EVIDENCE_ROOT="$VK_DEFAULT_BASE_DIR"
# shellcheck source=verify-lib.sh disable=SC1091
. "$(cd "$(dirname "$0")" && pwd)/verify-lib.sh"

PROJECT="aifoundry-simulator-verify"
API_KEY="local-foundry-key-alpha"
ADMIN_KEY="local-foundry-admin-key"
SMOKE_SCRIPTS="smoke_foundry smoke_semantic_cache smoke_content_safety"
CHAT_PATH='/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21'
API_KEY_HEADER="api-key: $API_KEY"
CAPITAL_BODY='{"messages":[{"role":"user","content":"What is the capital of France?"}]}'
PARAPHRASE_BODY='{"messages":[{"role":"user","content":"what is the capital of france"}]}'
LAST_OUT=""

vk_commands_help() {
  cat <<'HELP'
  launch                 docker compose up --build for this run on 127.0.0.1:8120 (VERIFY_PORT)
  doctor                 read-only: this run launched, our container owns the port on loopback, health OK
  request <METHOD> <path> [--json BODY] [Header:Value ...]
                         record one HTTP exchange (status, headers, body) as evidence
  cli <foundrysim args>  run the foundrysim client against our instance (transcript as evidence)
  smoke                  run smoke_foundry, smoke_semantic_cache and smoke_content_safety
  cleanup                save logs and compose down our project (only if this run launched); evidence is kept
HELP
}

vk_env_help() {
  printf '  VERIFY_PORT            port for a new run (default 8120; make up uses 8020)\n'
}

vk_sample_commands() {
  printf '%s\n' verify launch doctor "request POST /foundry/health" "cli cache-stats" smoke cleanup
}

vk_paths() {
  STATE="$VK_BASE_DIR/$VK_RUN_ID/state"
  EVIDENCE="$VK_EVIDENCE_ROOT/$VK_RUN_ID/evidence"
  PORT="$(cat "$STATE/port" 2>/dev/null || echo "${VERIFY_PORT:-8120}")"
  URL="http://127.0.0.1:$PORT"
}

# compose.yml publishes "${FOUNDRY_BIND_ADDRESS:-127.0.0.1}:${FOUNDRY_PORT}:8000"; pin loopback explicitly
# so an exported FOUNDRY_BIND_ADDRESS in the caller's shell cannot widen the verification instance.
compose() { (cd "$VK_ROOT" && FOUNDRY_BIND_ADDRESS=127.0.0.1 FOUNDRY_PORT="$PORT" docker compose -p "$PROJECT" "$@"); }

vk_plan() {
  case "$1" in
    verify) vk_plan_line "launch, doctor, cli cache-flush, chat miss, paraphrase hit, cli cache-stats (hits 1, misses 1), smoke x3, cleanup; evidence in $EVIDENCE" ;;
    launch) vk_plan_line "FOUNDRY_BIND_ADDRESS=127.0.0.1 FOUNDRY_PORT=$PORT docker compose -p $PROJECT up --build --detach --wait; write $STATE and $EVIDENCE" ;;
    doctor) vk_plan_line "read-only: check $STATE exists, the owner of $URL is $PROJECT-aifoundry-simulator-1 on 127.0.0.1, and $URL/foundry/health" ;;
    request) vk_plan_line "send ${2:-<METHOD>} ${3:-<path>} to $URL and record request, status, headers and body under $EVIDENCE" ;;
    cli) shift; vk_plan_line "uv run foundrysim --base-url $URL ${*:-<args>}; record transcript and exit code under $EVIDENCE" ;;
    smoke) vk_plan_line "run scripts/{$SMOKE_SCRIPTS}.py with SMOKE_FOUNDRY_BASE_URL=$URL; record transcripts and exit codes under $EVIDENCE" ;;
    cleanup) vk_plan_line "docker compose -p $PROJECT logs and down --remove-orphans (only if this run launched); rm -rf $STATE; keep $EVIDENCE" ;;
    *) vk_unknown_command ;;
  esac
}

require_launched() {
  [ -d "$STATE" ] || vk_fail "this run did not launch; run launch first"
}

cmd_launch() {
  if docker compose ls --format json 2>/dev/null | grep -q "\"Name\":\"$PROJECT\""; then
    vk_fail "compose project $PROJECT already running; run cleanup first"; return 1
  fi
  if vk_port_busy "$PORT"; then vk_fail "port $PORT in use; set VERIFY_PORT"; return 1; fi
  mkdir -p "$STATE" "$EVIDENCE"
  vk_mark_current
  echo "$PORT" > "$STATE/port"
  vk_record_revision "$EVIDENCE/revision.txt"
  compose up --build --detach --wait > "$EVIDENCE/launch.log" 2>&1 \
    || { vk_fail "compose up failed; see $EVIDENCE/launch.log"; return 1; }
  vk_say "ready run=$VK_RUN_ID url=$URL project=$PROJECT"
}

cmd_doctor() {
  vk_say "run=$VK_RUN_ID url=$URL"
  [ -d "$STATE" ] || { vk_fail "this run did not launch"; return 1; }
  local owner binding body
  owner="$(docker ps --filter "publish=$PORT" --format '{{.Names}}' | head -1)" || owner=""
  [ "$owner" = "$PROJECT-aifoundry-simulator-1" ] || { vk_fail "port $PORT published by '${owner:-nothing}'"; return 1; }
  binding="$(docker port "$owner" 8000/tcp | head -1)"
  case "$binding" in
    127.0.0.1:*) ;;
    *) vk_fail "published on $binding, not loopback"; return 1 ;;
  esac
  body="$(curl -fsS "$URL/foundry/health")" || { vk_fail "/foundry/health"; return 1; }
  vk_say "revision $(head -1 "$EVIDENCE/revision.txt") (now $(git -C "$VK_ROOT" rev-parse HEAD))"
  vk_say "OK binding=$binding health=$body"
}

# record_request <METHOD> <path> [--json BODY] [Header:Value ...]: sets LAST_OUT; an HTTP error status is recorded, not fatal.
record_request() {
  local method="${1:?method}" path="${2:?path}" out code
  local args
  shift 2
  out="$EVIDENCE/$(vk_stamp)-request"; mkdir -p "$out"
  LAST_OUT="$out"
  args=(-sS -X "$method" -D "$out/response-headers.txt" -o "$out/response-body.txt" -w '%{http_code}')
  printf '%s %s\n' "$method" "$URL$path" > "$out/request.txt"
  while [ $# -gt 0 ]; do
    if [ "$1" = --json ]; then
      args+=(-H 'content-type: application/json' --data "${2:?body}"); printf 'body: %s\n' "$2" >> "$out/request.txt"; shift 2
    else
      args+=(-H "$1"); printf '%s\n' "$1" >> "$out/request.txt"; shift
    fi
  done
  code="$(curl "${args[@]}" "$URL$path")"; echo "$code" > "$out/status.txt"
}

cmd_request() {
  require_launched || return 1
  record_request "$@"
  vk_say "status=$(cat "$LAST_OUT/status.txt")"
  grep -i '^x-' "$LAST_OUT/response-headers.txt" || true
  head -c 600 "$LAST_OUT/response-body.txt"; echo
  vk_say "evidence: $LAST_OUT"
}

# record_cli <foundrysim args>: sets LAST_OUT and writes exit-code.txt; the caller decides what a non-zero exit means.
record_cli() {
  local out rc=0
  out="$EVIDENCE/$(vk_stamp)-cli"; mkdir -p "$out"
  LAST_OUT="$out"
  printf '%q ' foundrysim --base-url "$URL" "$@" > "$out/command.txt"
  (cd "$VK_ROOT" && uv run --extra dev foundrysim --base-url "$URL" --api-key "$API_KEY" --admin-key "$ADMIN_KEY" "$@") \
    > "$out/stdout.txt" 2> "$out/stderr.txt" || rc=$?
  echo "$rc" > "$out/exit-code.txt"
}

cmd_cli() {
  require_launched || return 1
  record_cli "$@"
  cat "$LAST_OUT/stdout.txt" "$LAST_OUT/stderr.txt"
  vk_say "exit=$(cat "$LAST_OUT/exit-code.txt") evidence: $LAST_OUT"
  return "$(cat "$LAST_OUT/exit-code.txt")"
}

# record_smoke: sets LAST_OUT; one transcript and exit-code file per script, plus the overall exit-code.txt.
record_smoke() {
  local out s src rc=0
  out="$EVIDENCE/$(vk_stamp)-smoke"; mkdir -p "$out"
  LAST_OUT="$out"
  for s in $SMOKE_SCRIPTS; do
    src=0
    (cd "$VK_ROOT" && SMOKE_FOUNDRY_BASE_URL="$URL" uv run python "scripts/$s.py") > "$out/$s.txt" 2>&1 || src=$?
    echo "$src" > "$out/$s.exit-code.txt"
    [ "$src" -eq 0 ] || rc=1
  done
  echo "$rc" > "$out/exit-code.txt"
}

cmd_smoke() {
  require_launched || return 1
  record_smoke
  local s
  for s in $SMOKE_SCRIPTS; do
    vk_say "$s exit=$(cat "$LAST_OUT/$s.exit-code.txt")"
    tail -3 "$LAST_OUT/$s.txt"
  done
  vk_say "evidence: $LAST_OUT"
  return "$(cat "$LAST_OUT/exit-code.txt")"
}

cmd_cleanup() {
  if [ -d "$STATE" ]; then
    compose logs --no-color > "$EVIDENCE/compose.log" 2>&1 || true
    compose down --remove-orphans > "$EVIDENCE/down.log" 2>&1 || true
  fi
  rm -rf "${STATE:?}"
  rmdir "$VK_BASE_DIR/$VK_RUN_ID" 2>/dev/null || true
  vk_clear_current
  vk_say "stopped run=$VK_RUN_ID project=$PROJECT; evidence kept at $EVIDENCE"
}

vk_verify_recipes() {
  record_cli cache-flush
  vk_expect_equals "cli cache-flush exit" "$(cat "$LAST_OUT/exit-code.txt")" 0

  record_request POST "$CHAT_PATH" --json "$CAPITAL_BODY" "$API_KEY_HEADER"
  vk_expect_equals "capital request status" "$(cat "$LAST_OUT/status.txt")" 200
  vk_expect_contains "$LAST_OUT/response-headers.txt" 'x-semantic-cache: miss'

  record_request POST "$CHAT_PATH" --json "$PARAPHRASE_BODY" "$API_KEY_HEADER"
  vk_expect_equals "paraphrase request status" "$(cat "$LAST_OUT/status.txt")" 200
  vk_expect_contains "$LAST_OUT/response-headers.txt" 'x-semantic-cache: hit'

  record_cli cache-stats
  vk_expect_equals "cli cache-stats exit" "$(cat "$LAST_OUT/exit-code.txt")" 0
  vk_expect_contains "$LAST_OUT/stdout.txt" '"hits": 1,'
  vk_expect_contains "$LAST_OUT/stdout.txt" '"misses": 1,'

  record_smoke
  local s
  for s in $SMOKE_SCRIPTS; do
    vk_expect_equals "$s exit" "$(cat "$LAST_OUT/$s.exit-code.txt")" 0
  done
  vk_expect_equals "smoke overall exit" "$(cat "$LAST_OUT/exit-code.txt")" 0
}

vk_dispatch() {
  local cmd="$1"
  shift
  case "$cmd" in
    launch) cmd_launch ;;
    doctor) cmd_doctor ;;
    request) cmd_request ${@+"$@"} ;;
    cli) cmd_cli ${@+"$@"} ;;
    smoke) cmd_smoke ;;
    cleanup) cmd_cleanup ;;
    *) vk_unknown_command ;;
  esac
}

vk_main "$@"
