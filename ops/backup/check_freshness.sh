#!/usr/bin/env bash
# Run independently (for example hourly) so a missed backup is detected even if backup.sh stops running.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$ROOT/ops/backup"
# shellcheck source=ops/backup/offsite_env.sh
source "$SCRIPT_DIR/offsite_env.sh"
BACKUP_FRESHNESS_HOURS="${BACKUP_FRESHNESS_HOURS:-26}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ajo-freshness.XXXXXX")"
LOG="$WORK/commands.log"
NOTIFIED=0

on_exit() {
  local rc=$?
  trap - EXIT
  if ((rc != 0)); then
    if ((NOTIFIED == 0)); then send_backup_alert failure; fi
    printf '%s\n' 'FAIL backup freshness check: check failed or backup is overdue; temporary diagnostics were discarded.' >&2
  else
    printf '%s\n' 'PASS backup freshness check.'
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap on_exit EXIT

[[ "$BACKUP_FRESHNESS_HOURS" =~ ^[0-9]{1,5}$ ]] &&
  ((10#$BACKUP_FRESHNESS_HOURS > 0 && 10#$BACKUP_FRESHNESS_HOURS <= 10000)) || {
  printf '%s\n' 'Invalid backup freshness threshold.' >&2
  exit 2
}
BACKUP_FRESHNESS_HOURS=$((10#$BACKUP_FRESHNESS_HOURS))
require_alert_configuration
configure_offsite
command -v rclone >/dev/null
command -v python3 >/dev/null
rclone lsf --files-only "$OFFSITE_REMOTE" >"$WORK/remote.list" 2>/dev/null
if ! python3 "$SCRIPT_DIR/freshness.py" \
  --now "$(date -u +%s)" \
  --max-age-hours "$BACKUP_FRESHNESS_HOURS" \
  <"$WORK/remote.list"; then
  send_backup_alert freshness
  NOTIFIED=1
  exit 1
fi
