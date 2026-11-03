#!/usr/bin/env bash
# Operator-run encrypted backup verification. It never changes a production database.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$ROOT/ops/backup"
# shellcheck source=ops/backup/offsite_env.sh
source "$SCRIPT_DIR/offsite_env.sh"
IDENTITY_FILE="${1:-}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:18}"
PG_PASSWORD="$(openssl rand -hex 24 2>/dev/null || true)"
APP_PASSWORD="$(openssl rand -hex 24 2>/dev/null || true)"
SCHEDULER_PASSWORD="$(openssl rand -hex 24 2>/dev/null || true)"
CONTAINER="ajo-backup-verify-${RANDOM}-${RANDOM}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ajo-restore.XXXXXX")"
LOG="$WORK/commands.log"

cleanup() {
  local rc=$?
  trap - EXIT
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if command -v shred >/dev/null 2>&1; then
    find "$WORK" -type f -exec shred -u {} + 2>/dev/null || true
  fi
  rm -rf "$WORK"
  if ((rc != 0)); then
    send_backup_alert failure
    printf '%s\n' 'FAIL restore verification: operation failed; temporary command diagnostics were discarded.' >&2
  else
    printf '%s\n' 'PASS restore verification: encrypted backup restored and verified.'
  fi
  exit "$rc"
}
trap cleanup EXIT

[[ "$#" == 1 ]] || {
  printf '%s\n' 'Usage: verify_restore.sh PRIVATE_AGE_IDENTITY_FILE' >&2
  exit 2
}
IDENTITY_FILE="$1"

require_tools() {
  local tool
  for tool in age docker find grep openssl psql rclone sha256sum sort tail tar; do
    command -v "$tool" >/dev/null
  done
  [[ -n "$IDENTITY_FILE" && -f "$IDENTITY_FILE" && -r "$IDENTITY_FILE" ]] || {
    printf '%s\n' 'Pass a readable private age identity file path as the only argument.' >&2
    return 2
  }
  [[ -n "$PG_PASSWORD" && -n "$APP_PASSWORD" && -n "$SCHEDULER_PASSWORD" ]]
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
  local url="$1"
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$ALEMBIC" upgrade head
}

compare_manifest() {
  local url="$1"
  local table expected actual
  while IFS=$'\t' read -r table expected; do
    [[ -n "$table" && "$expected" =~ ^[0-9]+$ ]] || return 1
    actual="$(psql "$url" -X -At -v ON_ERROR_STOP=1 \
      -c "SELECT count(*) FROM public.\"$table\";" 2>>"$LOG")"
    [[ "$actual" == "$expected" ]] || return 1
  done <"$WORK/manifest.tsv"
}

require_alert_configuration
configure_offsite
require_tools
PYTHON="${PYTHON:-python3}"
ALEMBIC="${ALEMBIC:-$ROOT/backend/.venv/bin/alembic}"

rclone lsf --files-only "$OFFSITE_REMOTE" >"$WORK/remote.list" 2>/dev/null
LATEST="$(grep -E '^ajo-[0-9]{8}T[0-9]{6}Z\.age$' "$WORK/remote.list" | sort | tail -n 1)"
[[ -n "$LATEST" ]]
rclone copyto "${OFFSITE_REMOTE%/}/$LATEST" "$WORK/$LATEST" >/dev/null 2>&1
rclone copyto "${OFFSITE_REMOTE%/}/$LATEST.sha256" "$WORK/$LATEST.sha256" >/dev/null 2>&1
(
  cd "$WORK"
  sha256sum --check --status "$LATEST.sha256"
)
# The private identity remains at the caller-supplied path and is never copied into WORK.
age --decrypt --identity "$IDENTITY_FILE" --output "$WORK/backup.tar" "$WORK/$LATEST" >/dev/null 2>&1
tar -xf "$WORK/backup.tar" -C "$WORK" >>"$LOG" 2>&1
DUMP="$(find "$WORK" -maxdepth 1 -type f -name '*.dump' -print -quit)"
[[ -n "$DUMP" && -s "$DUMP" && -s "$WORK/manifest.tsv" && -s "$WORK/dump.sha256" ]]
(
  cd "$WORK"
  sha256sum --check --status dump.sha256
)

# Restore into a new local database in a throwaway container; never use a remote target.
docker run -d --rm --name "$CONTAINER" -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB=postgres -p 127.0.0.1:0:5432 "$POSTGRES_IMAGE" >>"$LOG" 2>&1
until docker exec "$CONTAINER" pg_isready -U postgres -d postgres >/dev/null 2>&1; do sleep 1; done
PORT="$(docker port "$CONTAINER" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
[[ "$PORT" =~ ^[0-9]+$ ]]
OWNER_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo"
APP_URL="postgresql://ajo_app:${APP_PASSWORD}@127.0.0.1:${PORT}/ajo"
SCHEDULER_URL="postgresql://ajo_scheduler:${SCHEDULER_PASSWORD}@127.0.0.1:${PORT}/ajo"
REFERENCE_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo_reference"

create_databases
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
