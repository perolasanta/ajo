# Neon setup guide and learning log (Ajo)

Target: one Neon project in **AWS Europe (Frankfurt), `aws-eu-central-1`**, next to the Frankfurt VPS.

## Verification status (2026-10-31)
- Historical project notes report that the migrations and the original smoke test were run on local PostgreSQL 16. This batch did not independently reproduce that run.
- The Batch 5 validator and restore drill were run on a fresh, isolated local PostgreSQL 16.15 cluster. All six checks passed before restore and after `pg_dump`/`pg_restore` plus ACL restoration; the runbook's `ajo_app` read/write smoke also passed and rolled back its fixture row.
- No Batch 5 check has been run against Neon; no check is claimed as Neon-verified. The local target used direct loopback connections and therefore did not prove transaction-pooler behavior.
- Run Batch 5 only against a disposable Neon branch. Never use production credentials or the `main` branch for the validation run.
- The local reference verifies migration output and application logic, but cannot prove Neon PgBouncer behavior, Neon role membership/ownership, or the Neon backup/restore path. Those still require a real disposable Neon branch.

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

First create a **fresh local reference database** on the same PostgreSQL major version as the target, then apply the checked-in migrations to it. Disable python-dotenv explicitly so the Alembic environment cannot load a repository `.env` file:

```bash
createdb ajo_validate_reference
cd backend
PYTHON_DOTENV_DISABLED=1 DATABASE_URL_DIRECT="$LOCAL_REFERENCE_URL" .venv/bin/alembic upgrade head
```

Export the three target URLs (`DATABASE_URL` pooled as `ajo_app`, `DATABASE_URL_DIRECT` direct owner, and `DATABASE_URL_SCHEDULER` direct as `ajo_scheduler`) plus `NEON_VALIDATE_REFERENCE_URL` pointing to that local reference. The validator refuses a remote reference URL, does not load `.env`, does not print URLs, and rolls back its fixture writes. Its required branch label is a human confirmation only; it cannot identify the Neon branch from a connection URL.

```bash
cd backend
python scripts/neon_validate.py --confirm-disposable-branch batch-5-validation --phase before-restore
```

It prints six PASS/FAIL checks: Alembic head/schema comparison, RLS and transaction-context reuse, app/scheduler role safety and startup guard, transaction/session advisory locks, effective grants versus the migration-built reference, and an app-role read/write smoke. `tenant_directory` is intentionally global routing metadata and is exempt from tenant-data RLS; the validator checks the remaining tables that carry `cooperative_id`. On local PostgreSQL, checks 2 and 4 validate Postgres semantics only; the output explicitly says Neon PgBouncer remains unverified. The lock probe exercises the exact member and tenant advisory-lock primitives used by the withdrawal/audit paths; it is not a financial withdrawal API test. The script does not create or restore a backup; follow [the offsite restore drill](../ops/runbooks/restore.md#c-batch-5-backup-restore-and-acl-drill) and run it again with `--phase after-restore` against the restored database.

## 8. Normal deployment verification
```bash
make migrate     # alembic upgrade head on DATABASE_URL_DIRECT (reads the repo-root .env)
make smoke       # runs backend/scripts/neon_smoke_test.py
```
These are owner-operated commands, not checks run by the PR author. The original smoke test covers a subset of the Batch 5 assertions and loads `.env`; use `neon_validate.py` for the explicit-environment validation flow above. **Run latency measurements on the Frankfurt VPS too**: that is the latency that matters. Add the real numbers to the log below.

## 9. Backups and restore
- Check what restore window and instant-restore options your plan gives, and write them below.
- Nightly encrypted `pg_dump` to storage outside Neon and the VPS: `ops/backup/pg_backup.sh`. Use a `pg_dump` client version at least as new as the Neon server.
- The backup uses `--no-privileges`, so restore must run `ops/backup/restore_app_acl.sh` as the owner after roles exist and before API/worker start.
- Rehearse both restore routes monthly, including the Batch 5 drill (`ops/runbooks/restore.md`).

## 10. Checks that require a real Neon branch
- **1. Target schema/head:** compare the actual Neon branch after migrations with the fresh local reference; a local result cannot prove the target branch's state.
- **2. Transaction pooling:** only the Neon `-pooler` endpoint can prove tenant context clearing and cross-transaction connection reuse through Neon's PgBouncer.
- **3. Neon role metadata:** local PostgreSQL tests cannot establish actual Neon ownership, superuser/BYPASSRLS attributes, or membership in Neon-provided roles.
- **4. Locks:** transaction locks must be exercised through the Neon pooler, and scheduler session-lock release must be observed on a direct Neon connection.
- **5. Target grants:** inspect effective grants on the Neon branch before and after ACL restoration; the local reference only supplies the expected migration grants.
- **6. Backup/restore:** dump, restore to a new Neon database/branch, rerun ACL restoration, then read/write as `ajo_app` using the linked runbook.

## 11. Things to check on your plan (not verified here)
- Restore window and price of point-in-time recovery.
- IP allow-listing to the VPS address (which plans include it).
- Branch count and storage limits; compute-hours alert in billing.
- Neon Postgres major version offered, and a matching `pg_dump` on the VPS.

## 12. Learning log (fill in as you go)
| Date | Experiment | What I saw | Decision |
|---|---|---|---|
|  | Smoke test from laptop vs Frankfurt VPS (p50/p95) |  |  |
|  | First query after 10 minutes idle (scale-to-zero on staging) |  |  |
|  | Create branch from main, change data, reset branch to parent |  |  |
|  | Restore main to a point in time into a new branch |  |  |
|  | Prepared cache on pooled connection (DB_DISABLE_PREPARED_CACHE=false) |  |  |
|  | Compute usage after a day of staging traffic (cost estimate) |  |  |
