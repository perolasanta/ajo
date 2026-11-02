#!/usr/bin/env bash
# Operator-run encrypted backup verification. It never changes a production database.
set -Eeuo pipefail
umask 077

: "${BACKUP_REMOTE:?BACKUP_REMOTE is required}"
: "${BACKUP_AGE_IDENTITY_FILE:?BACKUP_AGE_IDENTITY_FILE is required}"

POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:18}"
BACKUP_ALERT_HOOK="${BACKUP_ALERT_HOOK:-}"
PG_PASSWORD="$(openssl rand -hex 24)"
APP_PASSWORD="$(openssl rand -hex 24)"
SCHEDULER_PASSWORD="$(openssl rand -hex 24)"
CONTAINER="ajo-backup-verify-${RANDOM}-${RANDOM}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ajo-restore.XXXXXX")"
LOG="$WORK/commands.log"
PORT=""

cleanup() {
  local rc=$?
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if command -v shred >/dev/null 2>&1; then
    find "$WORK" -type f -exec shred -u {} + 2>/dev/null || true
  fi
  rm -rf "$WORK"
  if ((rc != 0)); then
    if [[ -n "$BACKUP_ALERT_HOOK" ]]; then
      sh -c "$BACKUP_ALERT_HOOK" >/dev/null 2>&1 || true
    fi
    printf '%s\n' 'FAIL restore verification: see operator logs for the failure' >&2
  else
    printf '%s\n' 'PASS restore verification: backup restored and verified'
  fi
  exit "$rc"
}
trap cleanup EXIT

require_tools() {
  local tool
  for tool in age docker openssl psql rclone sha256sum tar; do
    command -v "$tool" >/dev/null
  done
  [[ -f "$BACKUP_AGE_IDENTITY_FILE" ]]
  docker image inspect "$POSTGRES_IMAGE" >/dev/null
}

create_databases() {
  docker exec "$CONTAINER" psql -U postgres -d postgres -X -v ON_ERROR_STOP=1 \
    -c 'CREATE DATABASE ajo' -c 'CREATE DATABASE ajo_reference'
}

set_role_passwords() {
  docker exec "$CONTAINER" psql -U postgres -d ajo -X -v ON_ERROR_STOP=1 \
    -v app_password="$APP_PASSWORD" \
    -v scheduler_password="$SCHEDULER_PASSWORD" \
    -c "ALTER ROLE ajo_app PASSWORD :'app_password'" \
    -c "ALTER ROLE ajo_scheduler PASSWORD :'scheduler_password'"
}

migrate_reference() {
  local url=$1
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$ALEMBIC" upgrade head
}

compare_manifest() {
  local url=$1
  local table expected actual
  while IFS=$'\t' read -r table expected; do
    [[ -n "$table" && "$expected" =~ ^[0-9]+$ ]] || return 1
    actual="$(psql "$url" -X -At -v ON_ERROR_STOP=1 \
      -c "SELECT count(*) FROM public.\"$table\";" 2>>"$LOG")"
    [[ "$actual" == "$expected" ]] || return 1
  done <"$WORK/manifest.tsv"
}

require_tools
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
ALEMBIC="${ALEMBIC:-$ROOT/backend/.venv/bin/alembic}"

rclone lsf --files-only "$BACKUP_REMOTE" >"$WORK/remote.list" 2>>"$LOG"
LATEST="$(grep -E '^ajo-[0-9]{8}T[0-9]{6}Z\.age$' "$WORK/remote.list" | sort | tail -n 1)"
[[ -n "$LATEST" ]]
rclone copyto "${BACKUP_REMOTE%/}/$LATEST" "$WORK/$LATEST" >>"$LOG" 2>&1
age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" --output "$WORK/backup.tar" "$WORK/$LATEST" >>"$LOG" 2>&1
tar -xf "$WORK/backup.tar" -C "$WORK" >>"$LOG" 2>&1
DUMP="$(find "$WORK" -maxdepth 1 -type f -name '*.dump' -print -quit)"
[[ -n "$DUMP" && -s "$DUMP" && -s "$WORK/manifest.tsv" ]]

# Restore into a new local database in a throwaway container; never use a remote target.
docker run -d --rm --name "$CONTAINER" -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB=postgres -p 127.0.0.1:0:5432 "$POSTGRES_IMAGE" >"$LOG" 2>&1
until docker exec "$CONTAINER" pg_isready -U postgres -d postgres >/dev/null 2>&1; do sleep 1; done
PORT="$(docker port "$CONTAINER" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
[[ "$PORT" =~ ^[0-9]+$ ]]
OWNER_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo"
APP_URL="postgresql://ajo_app:${APP_PASSWORD}@127.0.0.1:${PORT}/ajo"
SCHEDULER_URL="postgresql://ajo_scheduler:${SCHEDULER_PASSWORD}@127.0.0.1:${PORT}/ajo"
REFERENCE_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo_reference"

create_databases
# Roles are recreated only inside the throwaway verifier container.
docker exec -i "$CONTAINER" psql -U postgres -d ajo <"$ROOT/backend/scripts/neon_roles.sql" >>"$LOG" 2>&1
set_role_passwords
docker exec -i "$CONTAINER" pg_restore --no-owner --no-privileges --dbname=ajo <"$DUMP" >>"$LOG" 2>&1
env DATABASE_URL_DIRECT="$OWNER_URL" "$ROOT/ops/backup/restore_app_acl.sh" >>"$LOG" 2>&1
migrate_reference "$REFERENCE_URL" >>"$LOG" 2>&1
env \
  DATABASE_URL="$APP_URL" \
  DATABASE_URL_DIRECT="$OWNER_URL" \
  DATABASE_URL_SCHEDULER="$SCHEDULER_URL" \
  NEON_VALIDATE_REFERENCE_URL="$REFERENCE_URL" \
  PYTHON_DOTENV_DISABLED=1 "$PYTHON" "$ROOT/backend/scripts/neon_validate.py" \
  --confirm-disposable-branch backup-verify --phase after-restore >>"$LOG" 2>&1
compare_manifest "$OWNER_URL"
