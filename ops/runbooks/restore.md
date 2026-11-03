# Runbook: restore (rehearse monthly, record the result)

Two independent routes. Practise both.

## A. Neon restore (fast, same project)
1. Neon Console, project ajo, branch main: use Restore / history to pick a timestamp. Check the plan's restore window first.
2. Prefer restoring into a NEW branch and verifying before touching production.
3. Verify: run `python scripts/neon_smoke_test.py` against the branch and compare ledger totals.

## B. Offsite encrypted backup (disaster route)
1. Create a fresh Neon branch or isolated PostgreSQL 18 destination; never restore over the source or production database.
2. On an authorized operator machine, configure the S3 endpoint, region, access key ID, secret access key and private bucket through the documented environment variables. Use `ops/backup/pull_offsite_copy.sh LOCAL_DIRECTORY` to download new encrypted files, or run `ops/backup/verify_restore.sh /path/to/age-identity.txt` for the monthly decrypt/restore check. The verifier reads the supplied identity in place and does not copy or store it.
3. For a manual restore, verify the `.age.sha256` checksum, decrypt with age using an authorized primary or recovery identity, and restore the custom-format dump with `pg_restore --no-owner --no-privileges` into the fresh empty destination.
4. Re-run `scripts/neon_roles.sql` if roles are missing, then run `DATABASE_URL_DIRECT=... ops/backup/restore_app_acl.sh` as the database owner.
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
### Recorded pre-restore Neon validation (2026-11-02)

All six checks of `backend/scripts/neon_validate.py --phase before-restore` passed on a disposable Neon `dev`-branch database running PostgreSQL 18, using a fresh local PostgreSQL 18 reference. `ajo_scheduler` used a direct connection and its password was set by SQL on the Neon branch. The run verified pooled RLS isolation across 20 transaction reuses; app/scheduler role safety; advisory locks; schema and effective-grant matches against the migration-built reference; and `ajo_app` read/write access with the insert rolled back. This was a pre-restore validation only: the backup/restore/ACL drill, behavior under load, and production branch remain unverified. No URLs or passwords are recorded. See [the Neon guide](../../docs/neon.md#7-validate-a-disposable-branch) for the check-by-check results.

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

5. Confirm roles exist on this Neon branch (role definitions are branch-scoped). Use the direct host for `DATABASE_URL_SCHEDULER`; set the `ajo_scheduler` password with SQL on the branch and keep it only in the password manager. Create the local reference with the same PostgreSQL major version as the destination; the recorded validation used PostgreSQL 18 for both. Set `PYTHON_DOTENV_DISABLED=1` when running Alembic against the reference. Export the destination's `DATABASE_URL`, `DATABASE_URL_DIRECT`, and `DATABASE_URL_SCHEDULER`, plus `NEON_VALIDATE_REFERENCE_URL` for the local reference, in the same terminal that will run the validator. Keep URLs and passwords out of this runbook. Stop any scheduler holding the destination lock, then run:

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

These scripts require operator-managed environment variables outside the repository. Never put a database URL, S3 access key, webhook, Telegram bot token, age private identity, or populated environment file in GitHub Actions or this repository. Offsite storage is a private bucket in a dedicated Supabase project, accessed through its S3-compatible endpoint. `ops/backup/offsite_env.sh` configures rclone from environment variables only, with path-style addressing; no on-disk rclone profile is used.

The backup host's protected environment must provide `DATABASE_URL_DIRECT`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `BACKUP_S3_BUCKET`, `BACKUP_AGE_RECIPIENTS` (comma-separated primary and recovery public recipients), `BACKUP_SLACK_WEBHOOK_URL`, `BACKUP_TELEGRAM_BOT_TOKEN`, and `BACKUP_TELEGRAM_CHAT_ID`. Restrict this environment to the backup operator. Do not set or store an age private identity there. See [age key custody and rotation](../../docs/backup-keys.md).

`BACKUP_RETENTION_DAILY`, `BACKUP_RETENTION_WEEKLY`, and `BACKUP_RETENTION_MONTHLY` default to 7, 4, and 6 windows. `BACKUP_FILE_LIMIT_MB` defaults to 50 MB with warnings at 70% and failure at 90%; `BACKUP_BUCKET_CAPACITY_GB` defaults to 1 GB with warning at 70%. Percentages can be adjusted with `BACKUP_FILE_WARN_PERCENT`, `BACKUP_FILE_FAIL_PERCENT`, and `BACKUP_BUCKET_WARN_PERCENT`. Capacity is measured across the dedicated bucket; the configured budget alert is not a provider-enforced quota. `BACKUP_FRESHNESS_HOURS` defaults to 26 hours.

Example operator schedules (keep the environment file readable only by the operator):

```cron
15 1 * * * . /etc/ajo/backup.env && /opt/ajo/ops/backup/backup.sh >>/var/log/ajo-backup.log 2>&1
0 * * * * . /etc/ajo/backup.env && /opt/ajo/ops/backup/check_freshness.sh >>/var/log/ajo-backup-freshness.log 2>&1
```

Run the decrypt/restore verification monthly from an authorized operator machine, once with the primary identity and separately with the recovery identity. Source only the S3 and alert environment needed by the verifier; pass each private identity by path:

```sh
. /secure/operator/offsite-environment
/opt/ajo/ops/backup/verify_restore.sh /secure/offline-mounted/age-identity.txt
```

The verifier selects the newest encrypted `.age` artifact, validates its checksum, decrypts only inside a mode-700 temporary directory, verifies the dump checksum, restores into a local PostgreSQL 18 container, reapplies ACLs, runs `neon_validate.py --phase after-restore`, compares every manifest count, and removes the decrypted files and container on both success and failure. The age identity is read from the supplied path and never copied into the temporary directory. Command diagnostics stay in the protected temporary directory and are discarded at exit. Failure alerts go to both Slack and Telegram; the successful backup heartbeat goes only to Telegram. Alert text is fixed and contains no URLs, credentials, backup names, or database data.

For a laptop copy, configure the same S3 endpoint/region/access credentials and alert hooks, then run `ops/backup/pull_offsite_copy.sh LOCAL_DIRECTORY`. It downloads new `.age` objects and checksum companions only, does not overwrite existing local objects, and never deletes local files.
