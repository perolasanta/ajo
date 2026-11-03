#!/usr/bin/env bash
# Download new encrypted backup objects without deleting or overwriting local copies.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$ROOT/ops/backup"
# shellcheck source=ops/backup/offsite_env.sh
source "$SCRIPT_DIR/offsite_env.sh"
DESTINATION="${1:-}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ajo-pull.XXXXXX")"
LOG="$WORK/commands.log"

on_exit() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)); then
    send_backup_alert failure
    printf '%s\n' 'FAIL offsite copy: download failed; temporary diagnostics were discarded and local files were not deleted.' >&2
  else
    printf '%s\n' 'PASS offsite copy: new encrypted files downloaded; local files were retained.'
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap on_exit EXIT

[[ "$#" == 1 && -n "$DESTINATION" ]] || {
  printf '%s\n' 'Usage: pull_offsite_copy.sh LOCAL_DESTINATION' >&2
  exit 2
}
require_alert_configuration
configure_offsite
command -v rclone >/dev/null
mkdir -p "$DESTINATION"
# rclone copy is additive; --ignore-existing prevents changing an already-downloaded object.
rclone copy "$OFFSITE_REMOTE" "$DESTINATION" \
  --include '*.age' \
  --include '*.age.sha256' \
  --ignore-existing \
  --quiet >/dev/null 2>&1
