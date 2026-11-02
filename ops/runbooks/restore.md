# Runbook: restore (rehearse monthly, record the result)

Two independent routes. Practise both.

## A. Neon restore (fast, same project)
1. Neon Console, project ajo, branch main: use Restore / history to pick a timestamp. Check the plan's restore window first.
2. Prefer restoring into a NEW branch and verifying before touching production.
3. Verify: run `python scripts/neon_smoke_test.py` against the branch and compare ledger totals.

## B. Offsite dump (disaster route)
1. Create a fresh Neon branch or project in aws-eu-central-1.
2. `gpg --decrypt ajo-<stamp>.dump.gpg > ajo.dump`
3. `pg_restore --no-owner --dbname "$DATABASE_URL_DIRECT" ajo.dump`
4. Re-run `scripts/neon_roles.sql` if roles are missing, then run
   `DATABASE_URL_DIRECT=... ops/backup/restore_app_acl.sh` as the database owner.
5. Run `alembic current` and verify ledger balances and audit hash chain (Phase 1+).
6. Do not switch the app env vars until the restore-drill checklist below passes.

## Restore-drill checklist
- [ ] Connect with the `ajo_app` credential, not the owner or scheduler credential.
- [ ] Read smoke test: as `ajo_app`, run `SELECT count(*) FROM cooperatives`.
- [ ] Write smoke test: as `ajo_app`, perform a disposable tenant-scoped insert in a transaction and roll it back.
- [ ] Confirm `ajo_app` has `rolbypassrls = false` and is not a member of a bypass-RLS role.
- [ ] Confirm the scheduler uses `DATABASE_URL_SCHEDULER` and can acquire/release its advisory lock.
- [ ] Start API and worker and confirm their startup role checks pass.
- [ ] Record date, route, duration, result, and any problems before switching traffic.

Log each drill: date, route, time taken, result, problems.

## C. Batch 5: backup, restore, and ACL drill

Run this only on a disposable Neon branch (or isolated local PostgreSQL instance), never on `main` or production. Stop the API, worker, and scheduler for the destination. A restore dump may contain personal and financial data: use a protected temporary directory (`umask 077`) and delete the dump when the drill is complete. Keep source and destination URLs in environment variables or a password manager; do not paste them into chat or commit them.

### Backup role and RLS proof

The backup connection must be the owner or another role with `BYPASSRLS`. A runtime role such as `ajo_app`, `ajo_readonly`, or `ajo_scheduler` must not bypass RLS: a dump taken with one of those roles can fail or omit tenant rows because `pg_dump` does not establish the application's tenant context. The disposable drill proves this by attempting a non-bypass dump and requiring it to be rejected or to restore row counts different from the owner dump. It also records that the owner used for the real dump has `rolbypassrls = true`.

1. On a disposable Neon branch, create an empty destination database (for example `ajo_restore_drill`) and set `DATABASE_URL_DIRECT_RESTORE` to its direct owner URL. If the owner permits SQL database creation, connect to the source branch as owner and run `CREATE DATABASE ajo_restore_drill;`; otherwise create it through the Neon Console. Grant `CONNECT` on it to `ajo_app` and `ajo_scheduler` as owner. Set `DATABASE_URL_APP_RESTORE` to the pooled `ajo_app` URL for that database and `DATABASE_URL_SCHEDULER_RESTORE` to the direct `ajo_scheduler` URL. Confirm all roles exist on the destination branch. Do not use a populated destination database.
2. Dump the source database without owner or ACL records:

   ```bash
   umask 077
   pg_dump --format=custom --no-owner --no-privileges \
     --file="$BACKUP_FILE" "$SOURCE_DATABASE_URL_DIRECT"
   ```

3. Restore into the new, empty destination database. Do not restore over a live database:

   ```bash
   pg_restore --no-owner --no-privileges \
     --dbname="$DATABASE_URL_DIRECT_RESTORE" "$BACKUP_FILE"
   ```

4. Reapply application ACLs as the destination owner. The helper resets direct app-role schema, table, column, sequence, function, and database grants to the migration-derived allowlist (including later read-only tables and column-specific updates), then grants `CONNECT` to `ajo_app` for the current database:

   ```bash
   DATABASE_URL_DIRECT="$DATABASE_URL_DIRECT_RESTORE" \
     ops/backup/restore_app_acl.sh
   ```

5. Export the destination URLs as the validator's `DATABASE_URL`, `DATABASE_URL_DIRECT`, and `DATABASE_URL_SCHEDULER`. Set `NEON_VALIDATE_REFERENCE_URL` to the fresh local migration reference described in `docs/neon.md`. Stop any scheduler holding the destination lock, then run:

   ```bash
   python backend/scripts/neon_validate.py \
     --confirm-disposable-branch batch-5-restore-drill --phase after-restore
   ```

   Check 6 performs a read and tenant-scoped insert/read smoke as `ajo_app`, and rolls the write back. Checks 1 and 5 compare the restored schema and effective grants to the local migration-built reference. The validator does not create the dump, execute `pg_restore`, or prove that the operator used the stated source and destination; record those manual steps below.

6. Also record an explicit app-role smoke transcript, still on the disposable destination. These commands use a unique tenant row and roll it back; they do not leave test rows behind:

   ```bash
   VALIDATION_ID=$(python -c 'import uuid; print(uuid.uuid4())')
   VALIDATION_SLUG="restore-drill-${VALIDATION_ID//-/}"
   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
     -v validation_id="$VALIDATION_ID" -v validation_slug="$VALIDATION_SLUG" <<'SQL'
   BEGIN;
   SELECT count(*) FROM public.cooperatives;
   SELECT set_config('app.cooperative_id', :'validation_id', true);
   INSERT INTO public.cooperatives (id, name, slug)
     VALUES (:'validation_id'::uuid, 'restore drill smoke', :'validation_slug');
   SELECT count(*) FROM public.cooperatives WHERE id = :'validation_id'::uuid;
   ROLLBACK;
   SQL
   ```

7. Record source/destination labels (not URLs or passwords), dump/restore exit status, ACL helper result, validator output, smoke result, date, and duration. Remove the temporary dump and destroy the disposable destination database/branch after evidence is recorded.

Drill record: date ______; source label ______; destination label ______; dump/restore ______; ACL restore ______; validator ______; app read/write ______; duration ______; issues ______.

## D. Operator-run encrypted backups

The scripts below are documentation-only operational tools. They require an operator-owned environment file outside the repository; never put that file, a database URL, a private age key, or an rclone credential in GitHub Actions or this repository.

Required environment names are `DATABASE_URL_DIRECT`, `BACKUP_AGE_RECIPIENT` (public age key), `BACKUP_REMOTE` (rclone destination), and `BACKUP_AGE_IDENTITY_FILE` (private key path for verification only). Optional names are `BACKUP_ROOT`, `BACKUP_MIN_BYTES`, `BACKUP_RETENTION_DAILY`, `BACKUP_RETENTION_WEEKLY`, `BACKUP_RETENTION_MONTHLY`, `POSTGRES_IMAGE`, and `BACKUP_ALERT_HOOK`. The defaults retain 7 daily, 4 weekly, and 6 monthly windows. Keep the environment file readable only by the backup operator.

Run the backup and restore verifier separately, with no overlapping execution:

```cron
15 1 * * * . /etc/ajo/backup.env && /opt/ajo/ops/backup/backup.sh >>/var/log/ajo-backup.log 2>&1
45 2 * * 0 . /etc/ajo/backup.env && /opt/ajo/ops/backup/verify_restore.sh >>/var/log/ajo-restore-verify.log 2>&1
```

The backup script requires a PostgreSQL 18 `pg_dump` client, writes a custom-format dump manifest and checksum, encrypts a bundle with the age public key, copies only the encrypted artifacts offsite with rclone, and invokes the optional alert hook on failure. The verifier selects the latest encrypted `.age` artifact, decrypts it only inside a mode-700 temporary directory, restores into a local PostgreSQL 18 container, reapplies ACLs, runs `neon_validate.py --phase after-restore`, compares every manifest count, and securely removes decrypted files and the container on both success and failure.
