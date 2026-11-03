#!/usr/bin/env bash
# Operator-run encrypted logical backup. Never run this against a test database by accident.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$ROOT/ops/backup"
# shellcheck source=ops/backup/offsite_env.sh
source "$SCRIPT_DIR/offsite_env.sh"

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/ajo}"
BACKUP_MIN_BYTES="${BACKUP_MIN_BYTES:-1024}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
BACKUP_RETENTION_MONTHLY="${BACKUP_RETENTION_MONTHLY:-6}"
BACKUP_FILE_LIMIT_MB="${BACKUP_FILE_LIMIT_MB:-50}"
BACKUP_FILE_WARN_PERCENT="${BACKUP_FILE_WARN_PERCENT:-70}"
BACKUP_FILE_FAIL_PERCENT="${BACKUP_FILE_FAIL_PERCENT:-90}"
BACKUP_BUCKET_CAPACITY_GB="${BACKUP_BUCKET_CAPACITY_GB:-1}"
BACKUP_BUCKET_WARN_PERCENT="${BACKUP_BUCKET_WARN_PERCENT:-70}"
PYTHON="${PYTHON:-python3}"
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
  trap - EXIT
  if ((rc != 0)); then
    send_backup_alert failure
    printf '%s\n' 'FAIL backup: operation failed; temporary command diagnostics were discarded.' >&2
  else
    send_backup_alert heartbeat
    printf '%s\n' 'PASS backup: encrypted artifact uploaded and retention applied.'
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap on_exit EXIT

require_positive_int() {
  local name="$1" value="$2" max="$3"
  [[ "$value" =~ ^[0-9]{1,13}$ ]] || {
    printf 'Invalid numeric configuration: %s.\n' "$name" >&2
    return 2
  }
  value=$((10#$value))
  ((value > 0 && value <= max)) || {
    printf 'Invalid numeric configuration: %s.\n' "$name" >&2
    return 2
  }
}

require_percent() {
  local name="$1" value="$2"
  [[ "$value" =~ ^[0-9]{1,3}$ ]] || {
    printf 'Invalid percentage configuration: %s.\n' "$name" >&2
    return 2
  }
  value=$((10#$value))
  ((value >= 1 && value <= 99)) || {
    printf 'Invalid percentage configuration: %s.\n' "$name" >&2
    return 2
  }
}

load_recipients() {
  local raw recipient
  local -a raw_recipients
  local -A seen_recipients=()
  : "${BACKUP_AGE_RECIPIENTS:?BACKUP_AGE_RECIPIENTS is required}"
  IFS=',' read -r -a raw_recipients <<<"$BACKUP_AGE_RECIPIENTS"
  RECIPIENT_ARGS=()
  for raw in "${raw_recipients[@]}"; do
    recipient="${raw#"${raw%%[![:space:]]*}"}"
    recipient="${recipient%"${recipient##*[![:space:]]}"}"
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || {
      printf '%s\n' 'Invalid age recipient configuration.' >&2
      return 2
    }
    [[ -z "${seen_recipients[$recipient]+present}" ]] || {
      printf '%s\n' 'Age recipients must be distinct.' >&2
      return 2
    }
    seen_recipients["$recipient"]=1
    RECIPIENT_ARGS+=(--recipient "$recipient")
  done
  ((${#seen_recipients[@]} >= 2)) || {
    printf '%s\n' 'At least two age recipients are required.' >&2
    return 2
  }
}

check_configuration() {
  local value
  require_alert_configuration
  require_positive_int BACKUP_MIN_BYTES "$BACKUP_MIN_BYTES" 1000000000000
  require_positive_int BACKUP_FILE_LIMIT_MB "$BACKUP_FILE_LIMIT_MB" 100000
  require_positive_int BACKUP_BUCKET_CAPACITY_GB "$BACKUP_BUCKET_CAPACITY_GB" 100000
  require_percent BACKUP_FILE_WARN_PERCENT "$BACKUP_FILE_WARN_PERCENT"
  require_percent BACKUP_FILE_FAIL_PERCENT "$BACKUP_FILE_FAIL_PERCENT"
  require_percent BACKUP_BUCKET_WARN_PERCENT "$BACKUP_BUCKET_WARN_PERCENT"
  BACKUP_MIN_BYTES=$((10#$BACKUP_MIN_BYTES))
  BACKUP_FILE_LIMIT_MB=$((10#$BACKUP_FILE_LIMIT_MB))
  BACKUP_BUCKET_CAPACITY_GB=$((10#$BACKUP_BUCKET_CAPACITY_GB))
  BACKUP_FILE_WARN_PERCENT=$((10#$BACKUP_FILE_WARN_PERCENT))
  BACKUP_FILE_FAIL_PERCENT=$((10#$BACKUP_FILE_FAIL_PERCENT))
  BACKUP_BUCKET_WARN_PERCENT=$((10#$BACKUP_BUCKET_WARN_PERCENT))
  ((BACKUP_FILE_WARN_PERCENT < BACKUP_FILE_FAIL_PERCENT)) || {
    printf '%s\n' 'File warning percentage must be below the fail percentage.' >&2
    return 2
  }
  for value in "$BACKUP_RETENTION_DAILY" "$BACKUP_RETENTION_WEEKLY" "$BACKUP_RETENTION_MONTHLY"; do
    [[ "$value" =~ ^[0-9]{1,6}$ ]] && ((10#$value <= 100000)) || {
      printf '%s\n' 'Retention values must be non-negative integers.' >&2
      return 2
    }
  done
  load_recipients
  configure_offsite
  mkdir -p "$BACKUP_ROOT"
}

require_tools() {
  local tool pg_dump_major
  for tool in age grep pg_dump pg_restore psql rclone sed sha256sum stat tar "$PYTHON"; do
    command -v "$tool" >/dev/null
  done
  pg_dump_major="$(pg_dump --version | sed -E 's/.* ([0-9]+)(\..*)?/\1/')"
  [[ "$pg_dump_major" == "18" ]] || {
    printf '%s\n' 'PostgreSQL 18 pg_dump is required.' >&2
    return 2
  }
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

guard_file_size() {
  local file="$1" size limit_bytes warn_percent fail_percent
  size="$(stat -c %s "$file")"
  limit_bytes=$((BACKUP_FILE_LIMIT_MB * 1000000))
  warn_percent="$BACKUP_FILE_WARN_PERCENT"
  fail_percent="$BACKUP_FILE_FAIL_PERCENT"
  if ((size * 100 >= limit_bytes * fail_percent)); then
    printf '%s\n' 'Backup artifact reached its configured hard size threshold.' >&2
    return 1
  fi
  if ((size * 100 >= limit_bytes * warn_percent)); then
    printf '%s\n' 'WARN backup artifact is approaching its configured size limit.' >&2
    send_backup_alert size-warning
  fi
}

apply_retention() {
  rclone lsf --files-only "$OFFSITE_REMOTE" >"$WORK/remote.list" 2>/dev/null
  "$PYTHON" "$SCRIPT_DIR/retention.py" \
    --today "$(date -u +%F)" \
    --daily "$BACKUP_RETENTION_DAILY" \
    --weekly "$BACKUP_RETENTION_WEEKLY" \
    --monthly "$BACKUP_RETENTION_MONTHLY" \
    <"$WORK/remote.list" >"$WORK/delete.list"
  local file
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    rclone deletefile "${OFFSITE_REMOTE%/}/$file" >/dev/null 2>&1
    if grep -Fxq "$file.sha256" "$WORK/remote.list"; then
      rclone deletefile "${OFFSITE_REMOTE%/}/$file.sha256" >/dev/null 2>&1
    fi
  done <"$WORK/delete.list"
}

check_bucket_capacity() {
  local capacity_bytes remote_bytes
  capacity_bytes=$((BACKUP_BUCKET_CAPACITY_GB * 1000000000))
  rclone size --json "$OFFSITE_BUCKET" >"$WORK/bucket-size.json" 2>/dev/null
  remote_bytes="$("$PYTHON" -c 'import json,sys; value=json.load(sys.stdin)["bytes"]; print(int(value))' <"$WORK/bucket-size.json")"
  if ((remote_bytes * 100 >= capacity_bytes * BACKUP_BUCKET_WARN_PERCENT)); then
    printf '%s\n' 'WARN offsite bucket is approaching its configured capacity.' >&2
    send_backup_alert size-warning
  fi
}

: "${DATABASE_URL_DIRECT:?DATABASE_URL_DIRECT is required}"
check_configuration
require_tools

pg_dump --format=custom --no-owner --no-privileges --file="$DUMP" \
  "$DATABASE_URL_DIRECT" >>"$LOG" 2>&1
check_dump
write_manifest
(
  cd "$WORK"
  sha256sum "$(basename "$DUMP")" >dump.sha256
)
tar -cf "$BUNDLE" -C "$WORK" "$(basename "$DUMP")" manifest.tsv dump.sha256 >>"$LOG" 2>&1
# Keep age diagnostics out of logs: malformed recipients must never be echoed there.
age --encrypt "${RECIPIENT_ARGS[@]}" --output "$ENCRYPTED" "$BUNDLE" >/dev/null 2>&1
guard_file_size "$ENCRYPTED"
(
  cd "$WORK"
  sha256sum "$(basename "$ENCRYPTED")" >"${NAME}.age.sha256"
)
guard_file_size "$WORK/${NAME}.age.sha256"
rclone copyto "$ENCRYPTED" "${OFFSITE_REMOTE%/}/${NAME}.age" >/dev/null 2>&1
rclone copyto "$WORK/${NAME}.age.sha256" "${OFFSITE_REMOTE%/}/${NAME}.age.sha256" >/dev/null 2>&1
apply_retention
check_bucket_capacity
