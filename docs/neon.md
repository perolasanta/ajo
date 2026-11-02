# Neon setup guide and learning log (Ajo)

Target: one Neon project in **AWS Europe (Frankfurt), `aws-eu-central-1`**, next to the Frankfurt VPS.

## Verification status (2026-11-02)
- Historical project notes report that the migrations and the original smoke test were run on local PostgreSQL 16. This batch did not independently reproduce that run.
- The Batch 5 validator and restore drill were run on a fresh, isolated local PostgreSQL 16.15 cluster. All six checks passed before restore and after `pg_dump`/`pg_restore` plus ACL restoration; the runbook's `ajo_app` read/write smoke also passed and rolled back its fixture row.
- On 2026-11-02, all six checks of `backend/scripts/neon_validate.py --phase before-restore` passed against a disposable Neon `dev`-branch database running PostgreSQL 18, compared with a fresh local PostgreSQL 18 reference. The scheduler used a direct connection, with its role password set by SQL on that Neon branch.
- This Neon run verified tenant RLS isolation across 20 pooled transaction reuses; app and scheduler role safety; transaction and scheduler advisory locks; the target schema and `ajo_app` effective grants against the migration-built reference; and `ajo_app` read/write access.
- The backup/restore/ACL drill, behavior under load, and anything on the production branch remain unverified. The production branch was not accessed.
- Run Batch 5 only against a disposable Neon branch. Never use production credentials or the `main` branch for the validation run.

## 1. Create the project
1. Sign up at neon.com and create a project named `ajo`.
2. **Region: AWS Europe (Frankfurt).** The region is fixed per project; changing it later means a new project plus a migration.
3. Create the database `ajo` from the Console (not with `CREATE DATABASE` in SQL).
4. Use the plan that fits the phase: Free is fine for learning and Phase 0 to 1 development. Move the **production** project to a paid plan before any pilot (see step 2).

## 2. Production compute (paid plan)
- Disable **scale to zero** on the production branch. Neon suspends idle compute after 5 minutes by default, and a cold start on a Telegram or WhatsApp webhook is unacceptable.
- Start at the minimum size (0.25 CU) with autoscaling up to about 1 CU. Always-on 0.25 CU is roughly 0.25 x 720 h x $0.106 = about $19 a month before storage. Re-check pricing on neon.com/pricing.
- Staging and dev branches can keep scale-to-zero on to save money.

## 3. Roles (important: do this with SQL)
Roles created in the Console, CLI or API, including the project's default owner (e.g. `neondb_owner`), are members of `neon_superuser`, which has **BYPASSRLS**. Row-level security does not apply to them. Ajo's tenant isolation relies on RLS, so:

| Role | Created how | Used for |
|---|---|---|
| Neon project owner role (default, e.g. `neondb_owner`; actual name may vary by project) | by Neon | Alembic migrations and `pg_dump` only. **Never** used by API, worker, or scheduler |
| `ajo_app` | **SQL** (`backend/scripts/neon_roles.sql`) | API and worker, pooled connection, RLS applies |
| `ajo_readonly` | **SQL** | Reports and auditor exports |
| `ajo_scheduler` | **SQL** (`backend/scripts/neon_roles.sql`) | Scheduler's direct session advisory lock only; no `BYPASSRLS` |

Steps: open the Neon SQL Editor on branch `main`, database `ajo`, paste `backend/scripts/neon_roles.sql`, replace both `CHANGE_ME_*` passwords with long random values (`openssl rand -base64 32`), run it. Store the passwords in your password manager.

## 4. Branches
Roles belong to branches and are copied into child branches, so **create roles first, then branch**.
1. From `main` create `staging` and `dev`.
2. On each child branch reset the passwords of `ajo_app` (SQL: `ALTER ROLE ajo_app PASSWORD '...'`) and the owner role, so a leaked dev password cannot touch production.
3. A branch copies data. Never branch production data into dev without masking personal data (BVN, account numbers, phone numbers, salaries).
4. Use a branch for every risky migration and for the opening-balance import rehearsal.

## 5. Connection strings (.env)
In the Console choose Connect, pick the branch, database and role, and toggle **Connection pooling** to see both hosts.
- `DATABASE_URL`: **pooled** host (has `-pooler` in the hostname), user `ajo_app`.
- `DATABASE_URL_DIRECT`: **direct** host (no `-pooler`), the owner role.
- `DATABASE_URL_SCHEDULER`: **direct** host (no `-pooler`), user `ajo_scheduler`; provision its password separately.
- The Console only shows passwords for roles it manages. For `ajo_app` build the URL by hand from the host in the dialog:
  `postgresql://ajo_app:<password>@ep-xxxx-pooler.eu-central-1.aws.neon.tech/ajo?sslmode=require`
- The code strips `channel_binding` and handles `sslmode` for asyncpg automatically (`app/core/db.py`).

## 6. Pooling rules (Neon runs PgBouncer in transaction mode)
- Pooled connections do not support session `SET`/`RESET`, `LISTEN/NOTIFY`, SQL-level `PREPARE`, temp tables that outlive a transaction, or **session-level advisory locks**. Protocol-level prepared statements are supported by Neon's PgBouncer.
- Therefore: tenant context uses `set_config('app.cooperative_id', ..., true)` **inside a transaction** (`app.core.db.transaction()`); locks use `pg_advisory_xact_lock`; never rely on `search_path` set by the app (use schema-qualified names, role-level defaults).
- Migrations and `pg_dump` use `DATABASE_URL_DIRECT` only. The scheduler uses its separate `DATABASE_URL_SCHEDULER` connection.
- `DB_DISABLE_PREPARED_CACHE=true` is the safe default. The smoke test tells you whether the cache works on your pooler; if it passes, you may set it to `false`.

## 7. Validate a disposable branch

First create a **fresh local reference database** on the same PostgreSQL major version as the target, then apply the checked-in migrations to it. For the recorded Neon run, both target and reference ran PostgreSQL 18. Disable python-dotenv explicitly so the Alembic environment cannot load a repository `.env` file:

```bash
createdb ajo_validate_reference
cd backend
PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$LOCAL_REFERENCE_URL" .venv/bin/alembic upgrade head
```

Confirm the app and scheduler roles exist on the **target Neon branch**; role definitions are branch-scoped, so do not assume a role change on another branch is present here. `DATABASE_URL_SCHEDULER` must use the direct host and `ajo_scheduler`; set that role's password with SQL on the target branch and keep it only in the password manager. Export `DATABASE_URL` (pooled `ajo_app`), `DATABASE_URL_DIRECT` (direct owner), `DATABASE_URL_SCHEDULER` (direct `ajo_scheduler`), and `NEON_VALIDATE_REFERENCE_URL` (the local reference) in the **same terminal** that will run the validator. Do not put URLs or passwords in this guide. The validator refuses a remote reference URL, does not load `.env`, does not print URLs, and rolls back its fixture writes. Its required branch label is a human confirmation only; it cannot identify the Neon branch from a connection URL.

```bash
cd backend
python scripts/neon_validate.py --confirm-disposable-branch batch-5-validation --phase before-restore
```

It prints six PASS/FAIL checks. The recorded Neon run passed all six:

| # | Check | Neon result |
|---|---|---|
| 1 | Migration head and schema reference | PASS — Neon schema and Alembic head matched the fresh migration-built PostgreSQL 18 reference. |
| 2 | Pooled RLS isolation and connection reuse | PASS — tenant isolation held across 20 pooled transaction reuses. |
| 3 | App/scheduler role safety and startup guard | PASS — neither role was owner, superuser, `BYPASSRLS`, or a member of a bypass-RLS role; the app startup guard passed. |
| 4 | Transaction and scheduler session advisory locks | PASS — pooled transaction locks and the direct scheduler session lock checks passed. |
| 5 | Effective grants (`before-restore`) | PASS — `ajo_app` effective grants matched the migration-built reference before restore. |
| 6 | App read/write smoke (`before-restore`) | PASS — `ajo_app` read and tenant-scoped insert/read succeeded; the insert was rolled back. |

`tenant_directory` is intentionally global routing metadata and is exempt from tenant-data RLS; the validator checks the remaining tables that carry `cooperative_id`. On a local PostgreSQL target, checks 2 and 4 validate PostgreSQL semantics only and do not prove Neon PgBouncer behavior. The lock probe exercises the member and tenant advisory-lock primitives used by withdrawal/audit paths; it is not a financial withdrawal API test. The validator does not create or restore a backup. The backup/restore/ACL drill remains outstanding; follow [the offsite restore drill](../ops/runbooks/restore.md#c-batch-5-backup-restore-and-acl-drill) and run it with `--phase after-restore` against the restored database.

## 8. Normal deployment verification
```bash
make migrate     # alembic upgrade head on DATABASE_URL_DIRECT (reads the repo-root .env)
make smoke       # runs backend/scripts/neon_smoke_test.py
```
These are owner-operated commands, not checks run by the PR author. The original smoke test covers a subset of the Batch 5 assertions and loads `.env`; use `neon_validate.py` for the explicit-environment validation flow above. **Run latency measurements on the Frankfurt VPS too**: that is the latency that matters. Add the real numbers to the log below.

## 9. Backups and restore
- Check what restore window and instant-restore options your plan gives, and write them below.
- Nightly encrypted `pg_dump` to storage outside Neon and the VPS: `ops/backup/backup.sh`. It requires a PostgreSQL 18 client, an age public key, and an operator-configured rclone remote; retention and alert settings are environment-controlled.
- The backup uses `--no-privileges`, so restore must run `ops/backup/restore_app_acl.sh` as the owner after roles exist and before API/worker start.
- The backup role must be the owner or an equivalent role with `BYPASSRLS`; `ajo_app`, `ajo_readonly`, and `ajo_scheduler` intentionally cannot make complete tenant-independent dumps.
- Rehearse both restore routes monthly, including the fake-data Batch 5 drill and encrypted-backup verifier (`ops/runbooks/restore.md`).

The fake-data drill proves the checked-in migrations, role setup, row-count preservation, ledger balance check, audit-chain verification, ACL restoration, and validator behavior on a local PostgreSQL 18 container. It does not prove real Neon connectivity, point-in-time restore, the age private key, the rclone destination, retention, alert delivery, or production cutover. Those require an operator-run verification with the documented environment variables.

## 10. Remaining Neon validation
- The disposable Neon `dev` branch passed the six `before-restore` checks recorded in Section 7. This does not establish anything about the production branch, which was not accessed.
- **Backup/restore/ACL drill:** still outstanding. Dump, restore to a new disposable Neon database/branch, rerun ACL restoration, and validate it with `--phase after-restore` using the linked runbook.
- **Behavior under load:** not tested; the validation is a correctness/smoke check, not a load or capacity test.

## 11. Things to check on your plan (not verified here)
- Restore window and price of point-in-time recovery.
- IP allow-listing to the VPS address (which plans include it).
- Branch count and storage limits; compute-hours alert in billing.
- Production branch PostgreSQL major version and a matching `pg_dump` client on the VPS; the disposable dev-branch validation used PostgreSQL 18.

## 12. Learning log (fill in as you go)
| Date | Experiment | What I saw | Decision |
|---|---|---|---|
|  | Smoke test from laptop vs Frankfurt VPS (p50/p95) |  |  |
|  | First query after 10 minutes idle (scale-to-zero on staging) |  |  |
|  | Create branch from main, change data, reset branch to parent |  |  |
|  | Restore main to a point in time into a new branch |  |  |
|  | Prepared cache on pooled connection (DB_DISABLE_PREPARED_CACHE=false) |  |  |
|  | Compute usage after a day of staging traffic (cost estimate) |  |  |
