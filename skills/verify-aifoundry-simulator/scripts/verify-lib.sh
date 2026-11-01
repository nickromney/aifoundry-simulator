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

VK_KIT_VERSION="1.3.7"
VK_SCRIPT_NAME="control.sh"
VK_VERIFY_ACTIVE=0
VK_CLEANUP_ATTEMPTED=0
VK_CLEANUP_RESULT=not_attempted

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
    VK_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM-$RANDOM"
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
  local rc=0
  if ! { [ -f "$1" ] && [ -r "$1" ]; }; then vk_fail "cannot read evidence $1"; return 1; fi
  grep -F -q -- "$2" "$1" || rc=$?
  case "$rc" in
    1) vk_pass "$(basename "$1") has no '$2'" ;;
    0) vk_fail "$(basename "$1") contains '$2'" ;;
    *) vk_fail "cannot read evidence $1 (grep exit $rc)" ;;
  esac
}
vk_expect_equals() {   # <label> <actual> <expected>
  if [ "$2" = "$3" ]; then vk_pass "$1 = '$3'"; else vk_fail "$1 = '$2', expected '$3'"; fi
}
vk_expect_count() {   # <file> <fixed text> <expected line count>
  local n rc=0
  if ! { [ -f "$1" ] && [ -r "$1" ]; }; then vk_fail "cannot read evidence $1"; return 1; fi
  n="$(grep -F -c -- "$2" "$1")" || rc=$?
  [ "$rc" -le 1 ] || { vk_fail "cannot read evidence $1 (grep exit $rc)"; return 1; }
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

# Hash names, types, contents and symlink targets without following links or writing files.
vk_tree_digest() {   # <directory>
  [ -d "$1" ] || { vk_err "not a directory: $1"; return 1; }
  perl -MFile::Find -MDigest::SHA -e '
    use strict; use warnings;
    local $SIG{__WARN__} = sub { die @_ };
    my $root = shift; chdir $root or die "chdir: $!";
    my @paths;
    find({ no_chdir => 1, wanted => sub { push @paths, $File::Find::name } }, ".");
    my $sha = Digest::SHA->new(256);
    for my $path (sort @paths) {
      my $kind = -l $path ? "link" : -f $path ? "file" : -d $path ? "dir" : "other";
      $sha->add(length($path), ":", $path, $kind, "\0");
      if ($kind eq "file") {
        open my $fh, "<", $path or die "read $path: $!"; binmode $fh;
        my $file = Digest::SHA->new(256); $file->addfile($fh); close $fh or die "close: $!";
        $sha->add($file->hexdigest);
      } elsif ($kind eq "link") {
        my $target = readlink $path; defined $target or die "readlink: $!";
        $sha->add(length($target), ":", $target);
      } elsif ($kind eq "other") { die "unsupported file type: $path"; }
    }
    print $sha->hexdigest, "\n";
  ' "$1"
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
# Claims live outside project scratch state, which cleanup hooks may remove.
vk_claim_run() {
  VK_OWNER_DIR="$VK_BASE_DIR/.verify-kit-owners/$VK_RUN_ID"
  mkdir -p "$VK_BASE_DIR/.verify-kit-owners" "$VK_EVIDENCE_ROOT" 2>/dev/null || { vk_fail "cannot write $VK_BASE_DIR or $VK_EVIDENCE_ROOT"; return 1; }
  if ! mkdir "$VK_OWNER_DIR" 2>/dev/null; then
    vk_fail "run $VK_RUN_ID is already owned; cleanup that run before reuse"
    return 1
  fi
  if [ -e "$VK_BASE_DIR/$VK_RUN_ID" ] || [ -L "$VK_BASE_DIR/$VK_RUN_ID" ] || [ -e "$EVIDENCE" ] || [ -L "$EVIDENCE" ]; then
    rmdir "$VK_OWNER_DIR"
    vk_fail "run $VK_RUN_ID already has state or evidence; use a new run id"
    return 1
  fi
}
vk_release_run() { rmdir "$VK_BASE_DIR/.verify-kit-owners/$VK_RUN_ID" 2>/dev/null || true; }
vk_capture_source() {
  VK_SOURCE_REVISION="$(git -C "$VK_ROOT" rev-parse HEAD 2>/dev/null || printf 'not-a-git-checkout')"
  VK_SOURCE_DIGEST=unavailable
  if git -C "$VK_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    VK_SOURCE_DIGEST="$(
      set -o pipefail
      trap - EXIT
      local source_root source_temporary_base source_temporary
      source_root="$(cd "$VK_ROOT" && pwd -P)" || exit 1
      source_temporary_base="$(cd "${TMPDIR:-/tmp}" && pwd -P)" || exit 1
      if [[ "$source_temporary_base/" = "${source_root%/}/"* ]]; then
        vk_err "source capture requires TMPDIR outside the source repository"; exit 1
      fi
      source_temporary="$(mktemp -d "$source_temporary_base/verify-kit-source.XXXXXXXX")" || exit 1
      trap 'rm -rf "$source_temporary"' EXIT
      {
        git -C "$VK_ROOT" diff --binary HEAD 2>"$source_temporary/git.stderr" || {
          cat "$source_temporary/git.stderr" >&2; exit 1;
        }
        if [ -s "$source_temporary/git.stderr" ]; then
          vk_err "cannot capture source: git diff reported diagnostics"
          cat "$source_temporary/git.stderr" >&2; exit 1
        fi
        git -C "$VK_ROOT" ls-files --others --exclude-standard -z >"$source_temporary/paths" 2>"$source_temporary/git.stderr" || {
          cat "$source_temporary/git.stderr" >&2; exit 1;
        }
        if [ -s "$source_temporary/git.stderr" ]; then
          vk_err "cannot capture source: git ls-files reported diagnostics"
          cat "$source_temporary/git.stderr" >&2; exit 1
        fi
        local path
        while IFS= read -r -d '' path; do
          printf '%s\0' "$path"
          if [ -L "$VK_ROOT/$path" ]; then readlink "$VK_ROOT/$path" || exit 1
          elif [ -f "$VK_ROOT/$path" ]; then LC_ALL=C shasum -a 256 "$VK_ROOT/$path" || exit 1
          else vk_err "cannot capture source file: $path"; exit 1
          fi
        done < "$source_temporary/paths"
      } | LC_ALL=C shasum -a 256 | awk '{print $1}'
    )" || { VK_SOURCE_DIGEST=unavailable; return 1; }
  fi
}
# Validate a proposed exception and retain the checked record as evidence.
# This validates a record and policy; review provenance and package-manager behavior must be verified separately.
vk_validate_cooldown_exception() { # <record.json> <request.json> [<oci-index-or-manifest.json>]
  local today
  today="$(date -u +%Y-%m-%d)" || return 1
  [ -d "${EVIDENCE:-}" ] || { vk_err "missing evidence directory"; return 1; }
  [ ! -e "$EVIDENCE/cooldown-exception.json" ] && [ ! -L "$EVIDENCE/cooldown-exception.json" ] || { vk_err "cooldown evidence already exists"; return 1; }
  perl -MJSON::PP -MTime::Piece -MDigest::SHA -e '
    use strict; use warnings;
    my ($record_file, $request_file, $today, $evidence, $oci_manifest) = @ARGV;
    sub load_json {
      my ($file) = @_;
      open my $in, "<", $file or die "cannot read $file: $!\n";
      local $/; my $obj = eval { decode_json(<$in>) };
      die "invalid JSON object: $file\n" unless ref($obj) eq "HASH";
      return $obj;
    }
    sub value {
      my ($obj, $field) = @_;
      my $v = $obj->{$field};
      die "missing or invalid $field\n" unless defined($v) && !ref($v) && $v =~ /\S/ && $v !~ /\A\s|\s\z|[\x00-\x1f\x7f*?\[\]]/;
      return $v;
    }
    sub date_value {
      my ($v) = @_;
      die "invalid date\n" unless $v =~ /\A\d{4}-\d{2}-\d{2}\z/;
      my $d = eval { Time::Piece->strptime($v, "%Y-%m-%d") };
      die "invalid date\n" unless $d && $d->strftime("%Y-%m-%d") eq $v;
      return $v;
    }
    sub version {
      my ($v) = @_;
      die "unsupported version: $v\n" unless $v =~ /\A(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\z/;
      return [map { 0 + $_ } ($1, $2, $3)];
    }
    sub cmp_version {
      my ($a, $b) = @_;
      for my $i (0..2) { my $c = $a->[$i] <=> $b->[$i]; return $c if $c; }
      return 0;
    }
    my $record = load_json($record_file);
    my $request = load_json($request_file);
    for my $key (qw(adapter ecosystem package advisory affectedMin affectedMax fixedMin fixedMax target rationale review created expires verification rollback)) { value($record, $key); }
    for my $key (qw(adapter ecosystem package advisory target installed)) { value($request, $key); }
    my %adapters = map { $_ => 1 } qw(yarn npm bun oci);
    die "unsupported adapter: only Yarn 4.18.1, npm 11.12.1, Bun 1.4.4, and byte-verified OCI records are supported; see cooldown-exceptions.md\n" unless $adapters{$record->{adapter}};
    for my $key (qw(adapter ecosystem package advisory target)) {
      die "request mismatch: $key\n" unless $record->{$key} eq $request->{$key};
    }
    for my $key (qw(ecosystem package)) {
      die "invalid $key\n" unless $record->{$key} =~ m{\A[A-Za-z0-9@][A-Za-z0-9_.@/+-]*\z};
    }
    die "invalid advisory\n" unless $record->{advisory} =~ m{\A(?:CVE-\d{4}-\d{4,}|GHSA-[23456789cfghjmpqrvwx]{4}(?:-[23456789cfghjmpqrvwx]{4}){2}|https://[^\s]+)\z};
    if (exists($record->{digest}) || exists($request->{digest}) || $record->{adapter} eq "npm" || $record->{adapter} eq "oci") {
      my $digest = value($record, "digest");
      die "digest mismatch\n" unless $digest =~ /\Asha256:[0-9a-f]{64}\z/ && value($request, "digest") eq $digest;
    }
    my $oci_evidence;
    die "OCI manifest bytes are only valid for the OCI adapter\n" if $record->{adapter} ne "oci" && defined($oci_manifest) && length($oci_manifest);
    if ($record->{adapter} eq "oci") {
      die "OCI manifest bytes are required\n" unless defined($oci_manifest) && length($oci_manifest);
      my $reference = value($record, "reference");
      die "request mismatch: reference\n" unless $reference eq value($request, "reference");
      die "invalid OCI ecosystem\n" unless $record->{ecosystem} eq "oci";
      my ($repository, $tag) = $reference =~ m{\A([a-z0-9.-]+(?::[0-9]{1,5})?/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*):([0-9]+\.[0-9]+\.[0-9]+)\z};
      die "invalid OCI reference\n" unless defined($repository) && $repository eq $record->{package} && $tag eq $record->{target};
      die "unsafe OCI manifest path\n" if -l $oci_manifest || !-f $oci_manifest;
      open my $image, "<", $oci_manifest or die "cannot read OCI manifest bytes: $!\n";
      binmode $image;
      local $/; my $bytes = <$image>;
      close $image or die "cannot close OCI manifest bytes: $!\n";
      die "empty or oversized OCI manifest bytes\n" unless defined($bytes) && length($bytes) > 0 && length($bytes) <= 10 * 1024 * 1024;
      my $actual = "sha256:" . Digest::SHA->new(256)->add($bytes)->hexdigest;
      die "OCI manifest digest mismatch\n" unless $actual eq $record->{digest};
      my $image_json = eval { decode_json($bytes) };
      die "malformed OCI manifest JSON\n" unless ref($image_json) eq "HASH" && defined($image_json->{schemaVersion}) && !ref($image_json->{schemaVersion}) && JSON::PP->new->canonical->encode($image_json->{schemaVersion}) eq "2";
      my %index_types = map { $_ => 1 } qw(application/vnd.oci.image.index.v1+json application/vnd.docker.distribution.manifest.list.v2+json);
      my %manifest_types = map { $_ => 1 } qw(application/vnd.oci.image.manifest.v1+json application/vnd.docker.distribution.manifest.v2+json);
      my %descriptor_types = map { $_ => 1 } qw(application/vnd.oci.image.manifest.v1+json application/vnd.docker.distribution.manifest.v2+json);
      my %config_types = map { $_ => 1 } qw(application/vnd.oci.image.config.v1+json application/vnd.docker.container.image.v1+json);
      my %layer_types = map { $_ => 1 } qw(application/vnd.oci.image.layer.v1.tar application/vnd.oci.image.layer.v1.tar+gzip application/vnd.oci.image.layer.v1.tar+zstd application/vnd.oci.image.layer.nondistributable.v1.tar application/vnd.oci.image.layer.nondistributable.v1.tar+gzip application/vnd.oci.image.layer.nondistributable.v1.tar+zstd application/vnd.docker.image.rootfs.diff.tar application/vnd.docker.image.rootfs.diff.tar.gzip application/vnd.docker.image.rootfs.foreign.diff.tar.gzip);
      my $media_type = $image_json->{mediaType} || "";
      my $descriptor_count;
      my $valid_descriptor = sub {
        my ($descriptor, $allowed_types, $with_platform) = @_;
        die "malformed OCI descriptor\n" unless ref($descriptor) eq "HASH";
        my $size = defined($descriptor->{size}) && !ref($descriptor->{size}) ? JSON::PP->new->canonical->encode($descriptor->{size}) : "";
        die "malformed OCI descriptor\n" unless defined($descriptor->{mediaType}) && !ref($descriptor->{mediaType}) && $allowed_types->{$descriptor->{mediaType}} && defined($descriptor->{digest}) && !ref($descriptor->{digest}) && $descriptor->{digest} =~ /\Asha256:[0-9a-f]{64}\z/ && $size =~ /\A(?:0|[1-9][0-9]*)\z/;
        if ($with_platform) {
          die "OCI index descriptor lacks platform\n" unless ref($descriptor->{platform}) eq "HASH" && defined($descriptor->{platform}{os}) && $descriptor->{platform}{os} =~ /\A[a-z0-9._-]+\z/ && defined($descriptor->{platform}{architecture}) && $descriptor->{platform}{architecture} =~ /\A[a-z0-9._-]+\z/;
        }
      };
      if ($index_types{$media_type}) {
        die "malformed OCI index manifests\n" unless ref($image_json->{manifests}) eq "ARRAY" && @{$image_json->{manifests}};
        $valid_descriptor->($_, \%descriptor_types, 1) for @{$image_json->{manifests}};
        $descriptor_count = scalar @{$image_json->{manifests}};
      } elsif ($manifest_types{$media_type}) {
        $valid_descriptor->($image_json->{config}, \%config_types, 0);
        die "malformed OCI image layers\n" unless ref($image_json->{layers}) eq "ARRAY";
        $valid_descriptor->($_, \%layer_types, 0) for @{$image_json->{layers}};
        $descriptor_count = 1 + scalar @{$image_json->{layers}};
      } else {
        die "unsupported OCI media type\n";
      }
      $oci_evidence = {reference => $reference, digest => $actual, mediaType => $media_type, sizeBytes => length($bytes), descriptorCount => $descriptor_count, evidence => "cooldown-oci-image.json", bytes => $bytes};
    }
    if ($record->{adapter} eq "npm") {
      my $url = value($record, "tarballUrl");
      die "invalid or mismatched npm tarball URL\n" unless $url eq value($request, "tarballUrl") && $url =~ m{\A(?:https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/|http://127\.0\.0\.1:[0-9]{1,5}/)[A-Za-z0-9._~@%/-]+\z};
    }
    my ($created, $expires, $now) = map { date_value($_) } ($record->{created}, $record->{expires}, $today);
    die "exception inactive\n" unless $created le $now && $now le $expires;
    my ($min, $max, $fixed_min, $fixed_max, $target, $installed) = map { version($_) } ($record->{affectedMin}, $record->{affectedMax}, $record->{fixedMin}, $record->{fixedMax}, $record->{target}, $request->{installed});
    die "version outside reviewed ranges\n" unless cmp_version($min, $installed) <= 0 && cmp_version($installed, $max) <= 0 && cmp_version($max, $fixed_min) < 0 && cmp_version($fixed_min, $target) <= 0 && cmp_version($target, $fixed_max) <= 0;
    use Fcntl qw(O_WRONLY O_CREAT O_EXCL);
    my @created;
    eval {
      if ($oci_evidence) {
        require File::Basename;
        my $image_path = File::Basename::dirname($evidence) . "/cooldown-oci-image.json";
        sysopen(my $image_out, $image_path, O_WRONLY | O_CREAT | O_EXCL, 0600) or die "cannot create OCI bytes evidence: $!\n";
        push @created, $image_path;
        binmode $image_out;
        print {$image_out} $oci_evidence->{bytes} or die "cannot write OCI bytes evidence: $!\n";
        close $image_out or die "cannot close OCI bytes evidence: $!\n";
        delete $oci_evidence->{bytes};
      }
      sysopen(my $out, $evidence, O_WRONLY | O_CREAT | O_EXCL, 0600) or die "cannot create evidence: $!\n";
      push @created, $evidence;
      my $proof = {checkedAt => $now, exception => $record, request => $request};
      $proof->{ociImage} = $oci_evidence if $oci_evidence;
      print {$out} JSON::PP->new->canonical->pretty->encode($proof) or die "cannot write evidence: $!\n";
      close $out or die "cannot close evidence: $!\n";
    };
    if ($@) { my $error = $@; unlink $_ for reverse @created; die $error; }
  ' "$1" "$2" "$today" "$EVIDENCE/cooldown-exception.json" "${3:-}" || { vk_err "cooldown exception rejected"; return 1; }
  vk_say "PASS: scoped cooldown exception recorded in $EVIDENCE/cooldown-exception.json"
}

# Check every installed manifest, including nested dependencies, before retaining
# any resolver output. Accept only registry version specs and no bundled deps.
vk_check_cooldown_manifests() { # <node> <project-dir> <package> <target>
  local node_bin="$1" project="$2" package="$3" target="$4"
  env -i PATH="$(dirname "$node_bin"):/usr/bin:/bin" "$node_bin" -e '
    const fs = require("fs"), path = require("path");
    const [project, name, target] = process.argv.slice(1);
    const root = fs.realpathSync(project), seen = new Set();
    let found = false;
    function contained(p) {
      const actual = fs.realpathSync(p);
      if (!actual.startsWith(root + path.sep)) throw Error("dependency path leaves disposable project");
      return actual;
    }
    function inspectPackage(p) {
      const real = contained(p);
      if (seen.has(real)) return;
      seen.add(real);
      const manifest = JSON.parse(fs.readFileSync(contained(path.join(real, "package.json")), "utf8"));
      if (manifest.name === name) {
        if (manifest.version !== target) throw Error("another version of the reviewed package was installed");
        found = true;
      }
      for (const field of ["bundleDependencies", "bundledDependencies"]) {
        const value = manifest[field];
        if (value && (!Array.isArray(value) || value.length)) throw Error("bundled dependencies are not supported");
      }
      for (const field of ["dependencies", "optionalDependencies", "peerDependencies"]) {
        if (manifest[field] == null) continue;
        if (typeof manifest[field] !== "object" || Array.isArray(manifest[field])) throw Error("unsupported dependency map");
        for (const spec of Object.values(manifest[field])) {
          if (typeof spec !== "string" || !/^[0-9A-Za-z*^~<>=| .+-]+$/.test(spec)) throw Error("unsupported dependency specification");
        }
      }
      inspectModules(path.join(real, "node_modules"));
    }
    function inspectModules(p) {
      if (!fs.existsSync(p)) return;
      const dir = contained(p);
      for (const entry of fs.readdirSync(dir)) {
        if (entry.startsWith(".")) continue;
        const child = path.join(dir, entry);
        if (entry.startsWith("@")) {
          const scope = contained(child);
          for (const scoped of fs.readdirSync(scope)) inspectPackage(path.join(scope, scoped));
        } else inspectPackage(child);
      }
    }
    const direct = JSON.parse(fs.readFileSync(contained(path.join(root, "node_modules", name, "package.json")), "utf8"));
    if (direct.name !== name || direct.version !== target) throw Error("installed target mismatch");
    inspectModules(path.join(root, "node_modules"));
    if (!found) throw Error("target missing from installed dependencies");
  ' "$project" "$package" "$target"
}

# Run Yarn with no inherited Yarn, proxy, credential, or Node options.
vk_yarn_clean_environment() { # <node> <yarn> <rc-filename> [args...]
  local node_bin="$1" yarn="$2" rc="$3"
  shift 3
  env -i PATH="$(dirname "$node_bin"):/usr/bin:/bin" YARN_RC_FILENAME="$rc" YARN_IGNORE_PATH=1 "$node_bin" "$yarn" "$@"
}

# Resolve exactly one reviewed package in an isolated Yarn 4.18.1 project.
# The temporary project and its override are removed on exit; only evidence remains.
vk_resolve_cooldown_yarn() ( # <record.json> <request.json> <yarn-4.18.1.cjs> <registry-url>
  local record="$1" request="$2" yarn="$3" registry="$4" tmp package target node_bin rc
  case "$yarn" in /*) ;; *) vk_err "Yarn executable must be an absolute path"; return 1 ;; esac
  [ -f "$yarn" ] || { vk_err "missing Yarn executable"; return 1; }
  node_bin="$(command -v node)" || { vk_err "missing Node executable"; return 1; }
  [ "$(env -i PATH="$(dirname "$node_bin"):/usr/bin:/bin" "$node_bin" "$yarn" --version)" = "4.18.1" ] || { vk_err "Yarn 4.18.1 required"; return 1; }
  perl -e 'die "invalid registry URL\n" unless $ARGV[0] =~ m{\A(?:https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?|http://127\.0\.0\.1:[0-9]{1,5})\z}' "$registry" || return 1
  [ ! -e "$EVIDENCE/cooldown-yarn.lock" ] && [ ! -L "$EVIDENCE/cooldown-yarn.lock" ] || { vk_err "Yarn evidence already exists"; return 1; }
  [ ! -e "$EVIDENCE/cooldown-yarn.log" ] && [ ! -L "$EVIDENCE/cooldown-yarn.log" ] || { vk_err "Yarn evidence already exists"; return 1; }
  vk_validate_cooldown_exception "$record" "$request" || return 1
  # Read the immutable evidence copy that validation produced, not the input files.
  package="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{package}' "$EVIDENCE/cooldown-exception.json")" || return 1
  target="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{target}' "$EVIDENCE/cooldown-exception.json")" || return 1
  perl -MJSON::PP -e '
    local $/; my $e=decode_json(<>); my $r=$e->{request};
    die "unsupported Yarn request\n" unless $r->{adapter} eq "yarn" && $r->{ecosystem} eq "npm" && !exists($r->{digest}) && $r->{package} =~ m{\A(?:\@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\z};
  ' "$EVIDENCE/cooldown-exception.json" || return 1
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/vk-yarn-cooldown.XXXXXXXX")" || return 1
  # shellcheck disable=SC2329 # Called by the EXIT trap.
  vk_yarn_cleanup() {
    local status=$?
    trap - EXIT
    if ! rm -rf -- "$tmp" || [ -e "$tmp" ]; then
      vk_err "failed to remove temporary Yarn override: $tmp"
      exit 1
    fi
    exit "$status"
  }
  trap vk_yarn_cleanup EXIT
  trap 'exit 1' HUP INT TERM
  rc=".vk-$(basename "$tmp").yml"
  perl -MJSON::PP -e '
    my ($root,$pkg,$target,$registry,$rc) = @ARGV;
    my $json=JSON::PP->new->canonical->pretty;
    open my $manifest, ">", "$root/package.json" or die $!;
    print {$manifest} $json->encode({name => "vk-yarn-cooldown", private => JSON::PP::true, dependencies => {$pkg => $target}}); close $manifest or die $!;
    open my $config, ">", "$root/$rc" or die $!;
    print {$config} $json->encode({npmRegistryServer => $registry, npmMinimalAgeGate => "1w", npmPreapprovedPackages => ["$pkg\@$target"], nodeLinker => "node-modules", enableScripts => JSON::PP::false, enableGlobalCache => JSON::PP::false, enableTelemetry => JSON::PP::false, unsafeHttpWhitelist => ["127.0.0.1"]}); close $config or die $!;
  ' "$tmp" "$package" "$target" "$registry" "$rc" || return 1
  # The same-day check immediately before resolution prevents a stale record
  # from being used after its UTC expiry, even if setup crossed midnight.
  perl -MJSON::PP -e 'local $/; my $r=decode_json(<STDIN>)->{exception}; my $today=shift; die "exception expired\n" unless $r->{created} le $today && $today le $r->{expires}' "$(date -u +%Y-%m-%d)" < "$EVIDENCE/cooldown-exception.json" || return 1
  (cd "$tmp" && vk_yarn_clean_environment "$node_bin" "$yarn" "$rc" install --mode=skip-build) > "$EVIDENCE/cooldown-yarn.log" 2>&1 || { vk_err "isolated Yarn resolution failed; see cooldown-yarn.log"; return 1; }
  vk_check_cooldown_manifests "$node_bin" "$tmp" "$package" "$target" >> "$EVIDENCE/cooldown-yarn.log" 2>&1 || { vk_err "Yarn installed an unsupported dependency; see cooldown-yarn.log"; return 1; }
  cp "$tmp/yarn.lock" "$EVIDENCE/cooldown-yarn.lock" || return 1
  vk_say "PASS: Yarn resolved the reviewed target in a disposable project"
)

# Fetch is separate so the expiry boundary can be tested without networking.
vk_fetch_cooldown_tarball() { # <url> <destination>
  env -i PATH=/usr/bin:/bin curl -fsS --max-time 60 --output "$2" "$1"
}

# Resolve a reviewed npm tarball without disabling the registry age gate.
vk_resolve_cooldown_npm() ( # <record.json> <request.json> <absolute-node> <absolute-npm-cli> <registry-url>
  local record="$1" request="$2" node_bin="$3" npm_cli="$4" registry="$5" tmp package target digest url actual
  case "$node_bin:$npm_cli" in /*:/*) ;; *) vk_err "Node and npm paths must be absolute"; return 1 ;; esac
  [ -f "$node_bin" ] && [ -f "$npm_cli" ] || { vk_err "missing Node or npm executable"; return 1; }
  [ "$(env -i PATH=/usr/bin:/bin "$node_bin" "$npm_cli" --version)" = 11.12.1 ] || { vk_err "npm 11.12.1 required"; return 1; }
  perl -e 'die "invalid registry URL\n" unless $ARGV[0] =~ m{\A(?:https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?|http://127\.0\.0\.1:[0-9]{1,5})\z}' "$registry" || return 1
  [ ! -e "$EVIDENCE/cooldown-npm.lock" ] && [ ! -L "$EVIDENCE/cooldown-npm.lock" ] || { vk_err "npm evidence already exists"; return 1; }
  [ ! -e "$EVIDENCE/cooldown-npm.log" ] && [ ! -L "$EVIDENCE/cooldown-npm.log" ] || { vk_err "npm evidence already exists"; return 1; }
  vk_validate_cooldown_exception "$record" "$request" || return 1
  package="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{package}' "$EVIDENCE/cooldown-exception.json")" || return 1
  target="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{target}' "$EVIDENCE/cooldown-exception.json")" || return 1
  digest="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{digest}' "$EVIDENCE/cooldown-exception.json")" || return 1
  url="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{tarballUrl}' "$EVIDENCE/cooldown-exception.json")" || return 1
  perl -MJSON::PP -e '
    local $/; my $e=decode_json(<>); my $r=$e->{request}; my $x=$e->{exception};
    die "unsupported npm request\n" unless $r->{adapter} eq "npm" && $r->{ecosystem} eq "npm" && $r->{package} =~ m{\A(?:\@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\z} && ($r->{digest} || "") =~ /\Asha256:[0-9a-f]{64}\z/ && ($r->{tarballUrl} || "") eq ($x->{tarballUrl} || "") && ($r->{tarballUrl} || "") =~ m{\A(?:https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/|http://127\.0\.0\.1:[0-9]{1,5}/)[A-Za-z0-9._~@%/-]+\z};
  ' "$EVIDENCE/cooldown-exception.json" || return 1
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/vk-npm-cooldown.XXXXXXXX")" || return 1
  # shellcheck disable=SC2329 # Called by the EXIT trap.
  vk_npm_cleanup() {
    local status=$?
    trap - EXIT
    if ! rm -rf -- "$tmp" || [ -e "$tmp" ]; then vk_err "failed to remove temporary npm project: $tmp"; exit 1; fi
    exit "$status"
  }
  trap vk_npm_cleanup EXIT
  trap 'exit 1' HUP INT TERM
  mkdir -p "$tmp/cache"
  : > "$tmp/userconfig"
  : > "$tmp/globalconfig"
  printf 'min-release-age=7\n' > "$tmp/.npmrc"
  perl -MJSON::PP -e '
    my ($root,$pkg)=@ARGV; open my $out, ">", "$root/package.json" or die $!;
    print {$out} JSON::PP->new->canonical->pretty->encode({name=>"vk-npm-cooldown",version=>"1.0.0",private=>JSON::PP::true,dependencies=>{$pkg=>"file:target.tgz"}}); close $out or die $!;
  ' "$tmp" "$package" || return 1
  perl -MJSON::PP -e 'local $/; my $r=decode_json(<STDIN>)->{exception}; my $today=shift; die "exception expired\n" unless $r->{created} le $today && $today le $r->{expires}' "$(date -u +%Y-%m-%d)" < "$EVIDENCE/cooldown-exception.json" || return 1
  vk_fetch_cooldown_tarball "$url" "$tmp/target.tgz" || { vk_err "could not fetch reviewed tarball"; return 1; }
  actual="$(shasum -a 256 "$tmp/target.tgz" | awk '{print $1}')" || return 1
  [ "sha256:$actual" = "$digest" ] || { vk_err "reviewed tarball digest mismatch"; return 1; }
  perl -MJSON::PP -e 'local $/; my $r=decode_json(<STDIN>)->{exception}; my $today=shift; die "exception expired\n" unless $r->{created} le $today && $today le $r->{expires}' "$(date -u +%Y-%m-%d)" < "$EVIDENCE/cooldown-exception.json" || return 1
  (cd "$tmp" && env -i PATH="$(dirname "$node_bin"):/usr/bin:/bin" npm_config_registry="$registry" npm_config_cache="$tmp/cache" npm_config_userconfig="$tmp/userconfig" npm_config_globalconfig="$tmp/globalconfig" "$node_bin" "$npm_cli" install --ignore-scripts --no-audit --no-fund) > "$EVIDENCE/cooldown-npm.log" 2>&1 || { vk_err "isolated npm resolution failed; see cooldown-npm.log"; return 1; }
  vk_check_cooldown_manifests "$node_bin" "$tmp" "$package" "$target" >> "$EVIDENCE/cooldown-npm.log" 2>&1 || { vk_err "npm installed an unsupported dependency; see cooldown-npm.log"; return 1; }
  env -i PATH="$(dirname "$node_bin"):/usr/bin:/bin" "$node_bin" -e '
    const fs = require("fs"), path = require("path");
    const [root,name,target] = process.argv.slice(1);
    const installed = JSON.parse(fs.readFileSync(path.join(root,"node_modules",name,"package.json"),"utf8"));
    if (installed.name !== name || installed.version !== target) throw Error("installed target mismatch");
    const lock = JSON.parse(fs.readFileSync(path.join(root,"package-lock.json"),"utf8"));
    if (lock.lockfileVersion !== 3 || !lock.packages || Array.isArray(lock.packages)) throw Error("unsupported lockfile");
    let found=false;
    for (const [key, entry] of Object.entries(lock.packages)) {
      if (key === "") continue;
      if (!entry || typeof entry !== "object" || entry.link || typeof entry.version !== "string") throw Error("unsupported lock entry");
      if (entry.inBundle) throw Error("bundled dependencies are not supported");
      if (key.startsWith("/") || key.split("/").includes("..")) throw Error("unsafe lock path");
      for (const field of ["dependencies", "optionalDependencies", "peerDependencies"]) {
        if (entry[field] == null) continue;
        if (typeof entry[field] !== "object" || Array.isArray(entry[field])) throw Error("unsupported dependency map");
        for (const spec of Object.values(entry[field])) {
          if (typeof spec !== "string" || !/^[0-9A-Za-z*^~<>=| .+-]+$/.test(spec)) throw Error("unsupported dependency specification");
        }
      }
      const i=key.lastIndexOf("node_modules/");
      if (i < 0) throw Error("unsupported lock path");
      const actualName=entry.name || key.slice(i+"node_modules/".length);
      if (actualName === name) { if (entry.version !== target) throw Error("another version of the reviewed package was resolved"); found=true; }
      if (key === "node_modules/" + name) { if (entry.resolved !== "file:target.tgz") throw Error("target was not the reviewed tarball"); }
      else if (typeof entry.resolved !== "string" || !/^https?:\/\//.test(entry.resolved)) throw Error("unsupported transitive source");
    }
    if (!found) throw Error("target missing from lockfile");
  ' "$tmp" "$package" "$target" >> "$EVIDENCE/cooldown-npm.log" 2>&1 || { vk_err "npm lockfile contains an unreviewed resolution; see cooldown-npm.log"; return 1; }
  cp "$tmp/package-lock.json" "$EVIDENCE/cooldown-npm.lock" || return 1
  vk_say "PASS: npm resolved the reviewed tarball in a disposable project"
)

# Resolve one reviewed npm package in an isolated Bun 1.4.4 project. Bun's
# exact-version exclusion is checked against every lock entry before retention.
vk_resolve_cooldown_bun() ( # <record.json> <request.json> <bun-1.4.4> <registry-url>
  local record="$1" request="$2" bun="$3" registry="$4" tmp package target node_bin
  case "$bun" in /*) ;; *) vk_err "Bun executable must be an absolute path"; return 1 ;; esac
  [ -f "$bun" ] || { vk_err "missing Bun executable"; return 1; }
  node_bin="$(command -v node)" || { vk_err "missing Node executable"; return 1; }
  [ "$(env -i PATH=/usr/bin:/bin "$bun" --version)" = "1.4.4" ] || { vk_err "Bun 1.4.4 required"; return 1; }
  perl -e 'die "invalid registry URL\n" unless $ARGV[0] =~ m{\A(?:https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?|http://127\.0\.0\.1:[0-9]{1,5})\z}' "$registry" || return 1
  [ ! -e "$EVIDENCE/cooldown-bun.lock" ] && [ ! -L "$EVIDENCE/cooldown-bun.lock" ] || { vk_err "Bun evidence already exists"; return 1; }
  [ ! -e "$EVIDENCE/cooldown-bun.log" ] && [ ! -L "$EVIDENCE/cooldown-bun.log" ] || { vk_err "Bun evidence already exists"; return 1; }
  vk_validate_cooldown_exception "$record" "$request" || return 1
  package="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{package}' "$EVIDENCE/cooldown-exception.json")" || return 1
  target="$(perl -MJSON::PP -e 'local $/; print decode_json(<>)->{request}{target}' "$EVIDENCE/cooldown-exception.json")" || return 1
  perl -MJSON::PP -e '
    local $/; my $e=decode_json(<>); my $r=$e->{request};
    die "unsupported Bun request\n" unless $r->{adapter} eq "bun" && $r->{ecosystem} eq "npm" && !exists($r->{digest}) && $r->{package} =~ m{\A(?:\@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\z};
  ' "$EVIDENCE/cooldown-exception.json" || return 1
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/vk-bun-cooldown.XXXXXXXX")" || return 1
  # shellcheck disable=SC2329 # Called by the EXIT trap.
  vk_bun_cleanup() {
    local status=$?
    trap - EXIT
    if ! rm -rf -- "$tmp" || [ -e "$tmp" ]; then
      vk_err "failed to remove temporary Bun override: $tmp"
      exit 1
    fi
    exit "$status"
  }
  trap vk_bun_cleanup EXIT
  trap 'exit 1' HUP INT TERM
  mkdir -p "$tmp/config" "$tmp/cache"
  : > "$tmp/config/.bunfig.toml"
  perl -MJSON::PP -e '
    my ($root,$pkg,$target,$registry) = @ARGV;
    my $json=JSON::PP->new->canonical->pretty;
    open my $manifest, ">", "$root/package.json" or die $!;
    print {$manifest} $json->encode({name => "vk-bun-cooldown", private => JSON::PP::true, dependencies => {$pkg => $target}}); close $manifest or die $!;
    open my $config, ">", "$root/bunfig.toml" or die $!;
    print {$config} "[install]\nminimumReleaseAge = 604800\nminimumReleaseAgeExcludes = [", encode_json("$pkg\@$target"), "]\nregistry = ", encode_json($registry), "\n"; close $config or die $!;
  ' "$tmp" "$package" "$target" "$registry" || return 1
  perl -MJSON::PP -e 'local $/; my $r=decode_json(<STDIN>)->{exception}; my $today=shift; die "exception expired\n" unless $r->{created} le $today && $today le $r->{expires}' "$(date -u +%Y-%m-%d)" < "$EVIDENCE/cooldown-exception.json" || return 1
  (cd "$tmp" && env -i PATH=/usr/bin:/bin XDG_CONFIG_HOME="$tmp/config" BUN_INSTALL_CACHE_DIR="$tmp/cache" "$bun" install --ignore-scripts) > "$EVIDENCE/cooldown-bun.log" 2>&1 || { vk_err "isolated Bun resolution failed; see cooldown-bun.log"; return 1; }
  vk_check_cooldown_manifests "$node_bin" "$tmp" "$package" "$target" >> "$EVIDENCE/cooldown-bun.log" 2>&1 || { vk_err "Bun installed an unsupported dependency; see cooldown-bun.log"; return 1; }
  (cd "$tmp" && env -i PATH=/usr/bin:/bin XDG_CONFIG_HOME="$tmp/config" BUN_INSTALL_CACHE_DIR="$tmp/cache" "$bun" -e '
    const fs = require("fs"), path = require("path");
    const [root, name, target] = process.argv.slice(1);
    const installed = JSON.parse(fs.readFileSync(path.join(root, "node_modules", name, "package.json"), "utf8"));
    if (installed.name !== name || installed.version !== target) throw Error("installed target mismatch");
    const lock = Bun.JSONC.parse(fs.readFileSync(path.join(root, "bun.lock"), "utf8"));
    if (!lock.packages || typeof lock.packages !== "object" || Array.isArray(lock.packages)) throw Error("unsupported lockfile");
    let found = false;
    for (const value of Object.values(lock.packages)) {
      if (!Array.isArray(value) || typeof value[0] !== "string" || !/^(?:@[^/@]+\/)?[^/@]+@\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(value[0])) throw Error("unsupported lock entry");
      if (value[0].startsWith(name + "@")) {
        if (value[0] !== name + "@" + target) throw Error("another exempted version was resolved");
        found = true;
      }
    }
    if (!found) throw Error("target missing from lockfile");
  ' "$tmp" "$package" "$target") >> "$EVIDENCE/cooldown-bun.log" 2>&1 || { vk_err "Bun lockfile contains an unreviewed resolution; see cooldown-bun.log"; return 1; }
  cp "$tmp/bun.lock" "$EVIDENCE/cooldown-bun.lock" || return 1
  vk_say "PASS: Bun resolved the reviewed target in a disposable project"
)

# A project opts in by setting VK_SECURITY_SCOPE and writing security-proof.json
# in EVIDENCE. Validation checks the structure and retained files, not whether a
# project's assertions or observations are truthful.
vk_validate_security_proof() {
  local proof_file="$EVIDENCE/security-proof.json"
  VK_SECURITY_RESULT="$(perl -MJSON::PP -MCwd=realpath -MTime::Piece -MDigest::SHA -e '
    use strict; use warnings;
    my ($file, $root, $requested, $revision, $digest) = @ARGV;
    my %allowed = map { $_ => 1 } qw(source isolated deployed);
    die "invalid requested security scope\n" unless $allowed{$requested};
    my $base = realpath($root) or die "missing evidence root\n";
    die "unsafe security proof file\n" if -l $file || !-f $file;
    my $proof_path = realpath($file);
    die "unsafe security proof file\n" unless defined($proof_path) && $proof_path eq "$base/security-proof.json";
    open my $in, "<", $file or die "missing security proof: $!\n";
    local $/; my $proof = eval { decode_json(<$in>) };
    die "malformed security proof\n" unless ref($proof) eq "HASH";
    sub nonempty { defined($_[0]) && !ref($_[0]) && $_[0] =~ /\S/ }
    die "missing advisory or scopes\n" unless nonempty($proof->{advisory}) && ref($proof->{scopes}) eq "HASH";
    my %statuses = map { $_ => 1 } qw(passed failed blocked unverified);
    for my $name (qw(source isolated deployed)) {
      my $scope = $proof->{scopes}{$name};
      die "invalid $name scope\n" unless ref($scope) eq "HASH" && nonempty($scope->{status}) && $statuses{$scope->{status}};
      next unless $scope->{status} eq "passed";
      die "missing $name evidence\n" unless ref($scope->{evidence}) eq "ARRAY" && @{$scope->{evidence}};
      my %hashes;
      for my $path (@{$scope->{evidence}}) {
        die "invalid evidence path\n" unless nonempty($path) && $path !~ m{^/} && $path !~ m{(?:^|/)\.\.(?:/|$)};
        my $actual = realpath("$base/$path");
        die "metadata is not evidence\n" if defined($actual) && ($actual eq "$base/security-proof.json" || $actual eq "$base/run-result.json");
        die "missing or unsafe evidence: $path\n" unless defined($actual) && index($actual, "$base/") == 0 && -f $actual && -s $actual;
        open my $evidence, "<", $actual or die "cannot read evidence: $path\n";
        binmode $evidence;
        $hashes{$path} = Digest::SHA->new(256)->addfile($evidence)->hexdigest;
        close $evidence or die "cannot close evidence: $path\n";
      }
      $scope->{evidenceDigests} = \%hashes;
      my @fields = qw(command capturedAt);
      push @fields, qw(checkedRevision checkedSourceDigest) if $name eq "source";
      push @fields, qw(environment os architecture toolVersions runtimeVersion artifactIdentity) if $name ne "source";
      push @fields, qw(workloadIdentity servingRuntime) if $name eq "deployed";
      for my $key (@fields) { die "missing $name $key\n" unless nonempty($scope->{$key}); }
      my $time = eval { Time::Piece->strptime($scope->{capturedAt}, "%Y-%m-%dT%H:%M:%SZ") };
      die "invalid $name capturedAt\n" unless $time && $time->strftime("%Y-%m-%dT%H:%M:%SZ") eq $scope->{capturedAt};
      if ($name ne "source") {
        die "invalid $name artifactIdentity\n" unless $scope->{artifactIdentity} =~ /\A(?:sha256:[0-9a-f]{64}|git:[0-9a-f]{40}|git:[0-9a-f]{64})\z/;
        if ($scope->{artifactIdentity} =~ /\Agit:/) {
          die "git artifact does not match clean captured source\n" unless $scope->{artifactIdentity} eq "git:$revision" && $digest eq "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        }
      }
      die "invalid $name exitStatus\n" unless defined($scope->{exitStatus}) && !ref($scope->{exitStatus}) && $scope->{exitStatus} =~ /\A0\z/;
      if ($name eq "source") {
        die "source revision or digest mismatch\n" unless $revision ne "unavailable" && $revision ne "not-a-git-checkout" && $scope->{checkedRevision} eq $revision && $digest =~ /\A[0-9a-f]{64}\z/ && $scope->{checkedSourceDigest} eq $digest;
      }
      if ($name eq "deployed") {
        my $isolated = $proof->{scopes}{isolated};
        die "deployed artifact mismatch\n" unless ref($isolated) eq "HASH" && ($isolated->{status} || "") eq "passed" && $scope->{artifactIdentity} eq ($isolated->{artifactIdentity} || "");
      }
    }
    $proof->{requestedScope} = $requested;
    print JSON::PP->new->canonical->encode($proof);
    exit($proof->{scopes}{$requested}{status} eq "passed" ? 0 : 1);
  ' "$proof_file" "$EVIDENCE" "$VK_SECURITY_SCOPE" "${VK_SOURCE_REVISION:-unavailable}" "${VK_SOURCE_DIGEST:-unavailable}")" && return 0
  vk_err "security proof for $VK_SECURITY_SCOPE did not pass validation"
  return 1
}
vk_write_result() {   # <result> <exit code>
  [ -d "${EVIDENCE:-}" ] || return 0
  # The browser scope is requested scope. Project drivers own completed-feature proof.
  perl -MJSON::PP -e '
    my ($file, $version, $app, $run, $revision, $digest, $browsers, $result, $exit, $cleanup, $scope, $security) = @ARGV;
    open my $out, ">", $file or die "write result: $!";
    my $record = {

      schemaVersion => 1, kitVersion => $version, app => $app, runId => $run,
      revision => $revision, sourceDigest => $digest, requestedBrowsers => $browsers,
      result => $result, exitCode => 0 + $exit, cleanup => $cleanup,
      completedFeatures => undef,
      featureEvidence => "See project driver artifacts for completed actions and browser versions"
    };
    if (length $scope) {
      $record->{securityProof} = length($security) ? decode_json($security) : { requestedScope => $scope, validation => "unverified" };
    }
    print {$out} JSON::PP->new->canonical->pretty->encode($record);
    close $out or die "close result: $!";
  ' "$EVIDENCE/run-result.json" "$VK_KIT_VERSION" "$VK_APP" "$VK_RUN_ID" \
    "${VK_SOURCE_REVISION:-unavailable}" "${VK_SOURCE_DIGEST:-unavailable}" \
    "${VK_REQUESTED_BROWSERS:-not-applicable}" "$1" "$2" "$VK_CLEANUP_RESULT" \
    "${VK_SECURITY_SCOPE:-}" "${VK_SECURITY_RESULT:-}"
}
vk_cleanup_owned() {
  # Keep project cleanup output visible and retain the claim on a failed hook.
  local rc=0 restore_errexit=0
  case $- in *e*) restore_errexit=1 ;; esac
  VK_CLEANUP_ATTEMPTED=1
  set +e
  ( set -e; trap - EXIT; cmd_cleanup )
  rc=$?
  if [ "$restore_errexit" -eq 1 ]; then set -e; fi
  if [ "$rc" -ne 0 ]; then
    VK_CLEANUP_RESULT=failed
    vk_err "cleanup failed run=$VK_RUN_ID exit=$rc; retry --execute cleanup with VERIFY_RUN_ID=$VK_RUN_ID"
    return "$rc"
  fi
  VK_CLEANUP_RESULT=passed
  vk_release_run
}
vk_on_verify_exit() {
  local rc=$?
  trap - EXIT
  if [ "$VK_VERIFY_ACTIVE" -eq 1 ] && [ "$rc" -eq 0 ]; then rc=1; fi
  if [ "$VK_CLEANUP_ATTEMPTED" -eq 0 ]; then
    set +e
    vk_cleanup_owned
  fi
  if [ -n "${VK_SECURITY_SCOPE:-}" ]; then
    vk_validate_security_proof || :
  fi
  if [ -n "${VK_SECURITY_BEFORE_CLEANUP:-}" ] && [ "${VK_SECURITY_RESULT:-}" != "$VK_SECURITY_BEFORE_CLEANUP" ]; then
    VK_SECURITY_RETENTION_FAILED=1
  fi
  if [ "${VK_SECURITY_RETENTION_FAILED:-0}" -eq 1 ]; then VK_SECURITY_RESULT=""; fi
  vk_write_result FAIL "$rc" || vk_err "could not retain run-result.json"
  vk_say "VERIFY FAIL run=$VK_RUN_ID exit=$rc evidence=${EVIDENCE:-unknown}" >&2
  exit "$rc"
}
vk_run_verify() {
  VK_SECURITY_RESULT=""
  VK_SECURITY_RETENTION_FAILED=0
  VK_SECURITY_BEFORE_CLEANUP=""
  vk_claim_run
  VK_VERIFY_ACTIVE=1
  trap vk_on_verify_exit EXIT
  vk_capture_source
  cmd_launch
  vk_write_result RUNNING 0
  cmd_doctor
  vk_verify_recipes
  if [ -n "${VK_SECURITY_SCOPE:-}" ]; then vk_validate_security_proof; fi
  VK_SECURITY_BEFORE_CLEANUP="${VK_SECURITY_RESULT:-}"
  local artifact
  local artifacts=()
  [ -d "$EVIDENCE" ] || { vk_fail "missing evidence directory $EVIDENCE"; return 1; }
  # The default evidence root can contain the hook's scratch STATE directory.
  # Inventory proof files while allowing cleanup to remove that scratch subtree.
  while IFS= read -r -d '' artifact; do artifacts[${#artifacts[@]}]="$artifact"; done < <(
    if [ -n "${STATE:-}" ] && [ "$STATE" != "$EVIDENCE" ]; then
      find "$EVIDENCE" -path "$STATE" -prune -o -type f -print0
    else
      find "$EVIDENCE" -type f -print0
    fi
  )
  vk_cleanup_owned
  for artifact in "${artifacts[@]+"${artifacts[@]}"}"; do
    [ -f "$artifact" ] || { vk_fail "evidence removed by cleanup: $artifact"; return 1; }
  done
  [ -d "$EVIDENCE" ] || { vk_fail "evidence removed by cleanup: $EVIDENCE"; return 1; }
  if [ -n "${VK_SECURITY_SCOPE:-}" ]; then
    vk_validate_security_proof
    if [ "$VK_SECURITY_RESULT" != "$VK_SECURITY_BEFORE_CLEANUP" ]; then
      VK_SECURITY_RETENTION_FAILED=1
      vk_fail "security proof or evidence changed during cleanup"
      return 1
    fi
  fi
  vk_write_result PASS 0
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
    if VK_REQUESTED_BROWSERS="$(vk_browsers)"; then :; else return $?; fi
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
    launch)
      vk_claim_run
      VK_VERIFY_ACTIVE=1
      trap vk_on_verify_exit EXIT
      vk_dispatch "$VK_CMD" ${@+"$@"}
      VK_VERIFY_ACTIVE=0
      trap - EXIT
      ;;
    cleanup)
      vk_dispatch "$VK_CMD" ${@+"$@"}
      vk_release_run
      ;;
    *)
      vk_dispatch "$VK_CMD" ${@+"$@"}
      ;;
  esac
}
