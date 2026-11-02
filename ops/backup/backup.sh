#!/usr/bin/env bash
# Operator-run encrypted logical backup. Never run this against a test database by accident.
set -Eeuo pipefail
umask 077

: "${DATABASE_URL_DIRECT:?DATABASE_URL_DIRECT is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required}"
: "${BACKUP_REMOTE:?BACKUP_REMOTE is required}"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/ajo}"
BACKUP_MIN_BYTES="${BACKUP_MIN_BYTES:-1024}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
BACKUP_RETENTION_MONTHLY="${BACKUP_RETENTION_MONTHLY:-6}"
BACKUP_ALERT_HOOK="${BACKUP_ALERT_HOOK:-}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="ajo-${STAMP}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ajo-backup.XXXXXX")"
LOG="$WORK/commands.log"
DUMP="$WORK/${NAME}.dump"
BUNDLE="$WORK/${NAME}.tar"
ENCRYPTED="$WORK/${NAME}.age"
MANIFEST="$WORK/manifest.tsv"

on_exit() {
  local rc=$?
  if ((rc != 0)); then
    if [[ -n "$BACKUP_ALERT_HOOK" ]]; then
      sh -c "$BACKUP_ALERT_HOOK" >>"$LOG" 2>&1 || true
    fi
    printf '%s\n' 'FAIL backup: see operator logs for the failure' >&2
  else
    printf '%s\n' 'PASS backup: encrypted bundle copied and retention applied'
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap on_exit EXIT

require_tools() {
  local tool
  for tool in age pg_dump pg_restore psql rclone sha256sum tar; do
    command -v "$tool" >/dev/null
  done
  [[ "$BACKUP_RETENTION_DAILY" =~ ^[0-9]+$ ]]
  [[ "$BACKUP_RETENTION_WEEKLY" =~ ^[0-9]+$ ]]
  [[ "$BACKUP_RETENTION_MONTHLY" =~ ^[0-9]+$ ]]
  local pg_dump_major
  pg_dump_major="$(pg_dump --version | sed -E 's/.* ([0-9]+)(\..*)?/\1/')"
  [[ "$pg_dump_major" == "18" ]]
  mkdir -p "$BACKUP_ROOT"
}

write_manifest() {
  : >"$MANIFEST"
  local table count
  while IFS= read -r table; do
    [[ -n "$table" ]] || continue
    count="$(psql "$DATABASE_URL_DIRECT" -X -At -v ON_ERROR_STOP=1 \
      -c "SELECT count(*) FROM public.\"$table\";" 2>>"$LOG")"
    printf '%s\t%s\n' "$table" "$count" >>"$MANIFEST"
  done < <(psql "$DATABASE_URL_DIRECT" -X -At -v ON_ERROR_STOP=1 \
    -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;" \
    2>>"$LOG")
  [[ -s "$MANIFEST" ]]
}

check_dump() {
  [[ "$(stat -c %s "$DUMP")" -ge "$BACKUP_MIN_BYTES" ]]
  pg_restore -l "$DUMP" >"$WORK/archive.list" 2>>"$LOG"
  grep -q 'TABLE DATA' "$WORK/archive.list"
}

apply_retention() {
  rclone lsf --files-only "$BACKUP_REMOTE" >"$WORK/remote.list" 2>>"$LOG"
  "$PYTHON" "$ROOT/ops/backup/retention.py" \
    --today "$(date -u +%F)" \
    --daily "$BACKUP_RETENTION_DAILY" \
    --weekly "$BACKUP_RETENTION_WEEKLY" \
    --monthly "$BACKUP_RETENTION_MONTHLY" \
    <"$WORK/remote.list" >"$WORK/delete.list"
  local file
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    rclone deletefile "${BACKUP_REMOTE%/}/$file" >>"$LOG" 2>&1
  done <"$WORK/delete.list"
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
require_tools

pg_dump --format=custom --no-owner --no-privileges --file="$DUMP" \
  "$DATABASE_URL_DIRECT" >>"$LOG" 2>&1
check_dump
write_manifest
sha256sum "$DUMP" >"$WORK/dump.sha256"
tar -cf "$BUNDLE" -C "$WORK" "$(basename "$DUMP")" manifest.tsv dump.sha256 >>"$LOG" 2>&1
age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$ENCRYPTED" "$BUNDLE" >>"$LOG" 2>&1
sha256sum "$ENCRYPTED" >"$WORK/${NAME}.age.sha256"
rclone copyto "$ENCRYPTED" "${BACKUP_REMOTE%/}/${NAME}.age" >>"$LOG" 2>&1
rclone copyto "$WORK/${NAME}.age.sha256" "${BACKUP_REMOTE%/}/${NAME}.age.sha256" >>"$LOG" 2>&1
apply_retention
