#!/usr/bin/env bash
# Disposable fake-data backup/restore drill. No external credentials are used.
# The PostgreSQL image must already exist locally; CI may pull it before invoking this script.
set -Eeuo pipefail

POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:latest}"
CONTAINER="ajo-restore-drill-${RANDOM}-${RANDOM}"
PG_PASSWORD="$(openssl rand -hex 24)"
APP_PASSWORD="$(openssl rand -hex 24)"
SCHEDULER_PASSWORD="$(openssl rand -hex 24)"
TMP_DIR="$(mktemp -d)"
LOG_FILE="$TMP_DIR/commands.log"
DUMP_FILE="$TMP_DIR/source.dump"
RESTRICTED_DUMP_FILE="$TMP_DIR/restricted.dump"
BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$BACKEND/.venv/bin/python}"
ALEMBIC="${ALEMBIC:-$BACKEND/.venv/bin/alembic}"
PORT=""

cleanup() {
  local rc=$?
  if [[ -n "$CONTAINER" ]]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
  if ((rc == 0)); then
    printf '%s\n' 'PASS restore drill: all checks passed'
  else
    printf '%s\n' 'FAIL restore drill: one or more checks failed' >&2
  fi
  exit "$rc"
}
trap cleanup EXIT

step() {
  local label=$1
  shift
  if "$@" >>"$LOG_FILE" 2>&1; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label" >&2
    tail -n 15 "$LOG_FILE" >&2
    return 1
  fi
}

require_tools() {
  command -v docker >/dev/null
  command -v openssl >/dev/null
  command -v psql >/dev/null
  command -v pg_restore >/dev/null
  [[ -x "$PYTHON" && -x "$ALEMBIC" ]]
  docker image inspect "$POSTGRES_IMAGE" >/dev/null
}

# Keep connection strings in process arguments/environment only; never print them.
run_psql() {
  local url=$1
  shift
  psql "$url" -X -v ON_ERROR_STOP=1 "$@"
}

create_databases() {
  docker exec "$CONTAINER" psql -U postgres -d postgres -X -v ON_ERROR_STOP=1 \
    -c 'CREATE DATABASE ajo' \
    -c 'CREATE DATABASE ajo_restore' \
    -c 'CREATE DATABASE ajo_restricted' \
    -c 'CREATE DATABASE ajo_reference'
}

set_role_passwords() {
  docker exec "$CONTAINER" psql -U postgres -d ajo -X -v ON_ERROR_STOP=1 \
    -v app_password="$APP_PASSWORD" \
    -v readonly_password="$APP_PASSWORD" \
    -v scheduler_password="$SCHEDULER_PASSWORD" \
    -c "ALTER ROLE ajo_app PASSWORD :'app_password'" \
    -c "ALTER ROLE ajo_readonly PASSWORD :'readonly_password'" \
    -c "ALTER ROLE ajo_scheduler PASSWORD :'scheduler_password'"
}

migrate_database() {
  local url=$1
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$ALEMBIC" upgrade head
}

seed_source() {
  local url=$1
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$PYTHON" -m app.cli \
    create-cooperative --slug restore-drill --name 'Restore Drill' \
    --admin-email admin@restore-drill.invalid >/dev/null
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$PYTHON" -m app.cli \
    seed-demo --slug restore-drill --yes-fake-data >/dev/null
}

write_counts() {
  local url=$1
  local output=$2
  : >"$output"
  local table count
  while IFS= read -r table; do
    [[ -n "$table" ]] || continue
    count="$(psql "$url" -X -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.\"$table\";")"
    printf '%s\t%s\n' "$table" "$count" >>"$output"
  done < <(psql "$url" -X -At -v ON_ERROR_STOP=1 -c \
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;")
}

check_ledger() {
  local url=$1
  local bad
  bad="$(psql "$url" -X -At -v ON_ERROR_STOP=1 -c \
    "SELECT count(*) FROM (SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING sum(debit_kobo) <> sum(credit_kobo)) unbalanced;")"
  [[ "$bad" == "0" ]]
}

verify_audit() {
  local url=$1
  local cooperative_id
  cooperative_id="$(psql "$url" -X -At -v ON_ERROR_STOP=1 -c \
    "SELECT cooperative_id FROM tenant_directory WHERE slug='restore-drill';")"
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$url" "$PYTHON" -m app.cli \
    verify-audit --cooperative-id "$cooperative_id" >/dev/null
}

check_backup_owner_bypasses_rls() {
  local bypass
  bypass="$(psql "$OWNER_URL" -X -At -v ON_ERROR_STOP=1 -c \
    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user;")"
  [[ "$bypass" == "t" ]]
}

prove_rls_risk() {
  # A dump taken as ajo_app runs under forced RLS and has no tenant context. It
  # must fail or restore fewer rows than the owner dump; equality is a hard fail.
  if docker exec -e PGPASSWORD="$APP_PASSWORD" "$CONTAINER" pg_dump \
    --username=ajo_app --format=custom --no-owner --no-privileges --dbname=ajo \
    >"$RESTRICTED_DUMP_FILE" 2>>"$LOG_FILE"; then
    [[ -s "$RESTRICTED_DUMP_FILE" ]]
    docker exec -i "$CONTAINER" pg_restore --no-owner --no-privileges \
      --dbname=ajo_restricted <"$RESTRICTED_DUMP_FILE" >>"$LOG_FILE" 2>&1 || return 0
    write_counts "$RESTRICTED_OWNER_URL" "$TMP_DIR/restricted.counts"
    if cmp -s "$TMP_DIR/source.counts" "$TMP_DIR/restricted.counts"; then
      return 1
    fi
    return 0
  fi
  return 0
}

require_tools
step 'start local PostgreSQL 18 container' docker run -d --rm --name "$CONTAINER" \
  -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=postgres -p 127.0.0.1:0:5432 "$POSTGRES_IMAGE"
PORT="$(docker port "$CONTAINER" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
[[ "$PORT" =~ ^[0-9]+$ ]]
step 'wait for PostgreSQL readiness' bash -c \
  "until docker exec '$CONTAINER' pg_isready -U postgres -d postgres >/dev/null 2>&1; do sleep 1; done"
OWNER_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo"
APP_URL="postgresql://ajo_app:${APP_PASSWORD}@127.0.0.1:${PORT}/ajo"
SCHEDULER_URL="postgresql://ajo_scheduler:${SCHEDULER_PASSWORD}@127.0.0.1:${PORT}/ajo"
RESTORE_OWNER_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo_restore"
RESTORE_APP_URL="postgresql://ajo_app:${APP_PASSWORD}@127.0.0.1:${PORT}/ajo_restore"
RESTORE_SCHEDULER_URL="postgresql://ajo_scheduler:${SCHEDULER_PASSWORD}@127.0.0.1:${PORT}/ajo_restore"
RESTRICTED_OWNER_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo_restricted"
REFERENCE_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PORT}/ajo_reference"

step 'create source and restore databases' create_databases
step 'create application roles from neon_roles.sql' docker exec -i "$CONTAINER" psql -U postgres -d ajo <"$ROOT/backend/scripts/neon_roles.sql"
step 'set throwaway role passwords' set_role_passwords
step 'run migrations on source database' migrate_database "$OWNER_URL"
step 'run migrations on local validator reference database' migrate_database "$REFERENCE_URL"
step 'seed source with fake demo data' seed_source "$OWNER_URL"
step 'record source table row counts' write_counts "$OWNER_URL" "$TMP_DIR/source.counts"
step 'verify source ledger balances' check_ledger "$OWNER_URL"
step 'verify source audit chain' verify_audit "$OWNER_URL"
step 'confirm backup owner bypasses RLS' check_backup_owner_bypasses_rls
step 'prove non-bypass RLS dump is incomplete or rejected' prove_rls_risk

step 'create custom-format source dump with container client' bash -c \
  "docker exec '$CONTAINER' pg_dump --format=custom --no-owner --no-privileges --dbname=ajo > '$DUMP_FILE'"
check_dump() {
  pg_restore -l "$DUMP_FILE" >"$TMP_DIR/archive.list" 2>>"$LOG_FILE"
  grep -q 'TABLE DATA' "$TMP_DIR/archive.list"
  [[ -s "$DUMP_FILE" ]]
}
step 'confirm dump contains table data' check_dump

step 'restore dump into fresh database with container client' bash -c \
  "docker exec -i '$CONTAINER' pg_restore --no-owner --no-privileges --dbname=ajo_restore < '$DUMP_FILE'"
step 'restore application ACLs' env DATABASE_URL_DIRECT="$RESTORE_OWNER_URL" "$ROOT/ops/backup/restore_app_acl.sh"
step 'validate restored database and grants' env \
  DATABASE_URL="$RESTORE_APP_URL" \
  DATABASE_URL_DIRECT="$RESTORE_OWNER_URL" \
  DATABASE_URL_SCHEDULER="$RESTORE_SCHEDULER_URL" \
  NEON_VALIDATE_REFERENCE_URL="$REFERENCE_URL" \
  PYTHON_DOTENV_DISABLED=1 "$PYTHON" "$ROOT/backend/scripts/neon_validate.py" \
  --confirm-disposable-branch restore-drill --phase after-restore
step 'record restored table row counts' write_counts "$RESTORE_OWNER_URL" "$TMP_DIR/restored.counts"
step 'compare source and restored row counts' cmp "$TMP_DIR/source.counts" "$TMP_DIR/restored.counts"
step 'verify restored ledger balances' check_ledger "$RESTORE_OWNER_URL"
step 'verify restored audit chain' verify_audit "$RESTORE_OWNER_URL"

# The RLS proof is item 2; item 1 deliberately stops before taking the restricted dump.
printf '%s\n' 'PASS restore drill: source and restored counts, ledger, audit, ACL, and validator checks passed'
