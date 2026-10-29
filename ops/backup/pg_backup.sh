#!/usr/bin/env bash
# Nightly encrypted logical backup of the Neon DIRECT connection, stored OUTSIDE Neon and the VPS.
# Cron (VPS):  15 1 * * * /opt/ajo/ops/backup/pg_backup.sh >> /var/log/ajo-backup.log 2>&1
# Needs: pg_dump 17+ client matching your Neon Postgres version, gpg, rclone (configured remote "offsite").
# --no-privileges intentionally keeps role passwords and ACLs out of the dump. After
# restore, run restore_app_acl.sh as the database owner before starting API/worker.
set -euo pipefail
: "${DATABASE_URL_DIRECT:?}" "${BACKUP_GPG_RECIPIENT:?}" "${BACKUP_REMOTE:=offsite:ajo-backups}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/tmp/ajo-${STAMP}.dump.gpg"
trap 'rm -f "$OUT"' EXIT
pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL_DIRECT" | gpg --encrypt --recipient "$BACKUP_GPG_RECIPIENT" --output "$OUT"
rclone copyto "$OUT" "${BACKUP_REMOTE}/ajo-${STAMP}.dump.gpg"
# Retention: keep 35 days
rclone delete --min-age 35d "${BACKUP_REMOTE}"
echo "backup ok ${STAMP}"
