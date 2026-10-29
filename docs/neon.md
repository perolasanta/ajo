# Neon setup guide and learning log (Ajo)

Target: one Neon project in **AWS Europe (Frankfurt), `aws-eu-central-1`**, next to the Frankfurt VPS.
Status of this guide: the SQL, migration and smoke test were verified against a local Postgres 16.
They have **not** yet run against Neon. Local Postgres has no PgBouncer, so Step 7 is the real proof.

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
| Neon project owner role (default, e.g. `neondb_owner`; actual name may vary by project) | by Neon | Alembic migrations, `pg_dump`, scheduler lock. **Never** used by the API |
| `ajo_app` | **SQL** (`backend/scripts/neon_roles.sql`) | API and worker, pooled connection, RLS applies |
| `ajo_readonly` | **SQL** | Reports and auditor exports |

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
- The Console only shows passwords for roles it manages. For `ajo_app` build the URL by hand from the host in the dialog:
  `postgresql://ajo_app:<password>@ep-xxxx-pooler.eu-central-1.aws.neon.tech/ajo?sslmode=require`
- The code strips `channel_binding` and handles `sslmode` for asyncpg automatically (`app/core/db.py`).

## 6. Pooling rules (Neon runs PgBouncer in transaction mode)
- Pooled connections do not support session `SET`/`RESET`, `LISTEN/NOTIFY`, SQL-level `PREPARE`, temp tables that outlive a transaction, or **session-level advisory locks**. Protocol-level prepared statements are supported by Neon's PgBouncer.
- Therefore: tenant context uses `set_config('app.cooperative_id', ..., true)` **inside a transaction** (`app.core.db.transaction()`); locks use `pg_advisory_xact_lock`; never rely on `search_path` set by the app (use schema-qualified names, role-level defaults).
- Migrations, `pg_dump` and the scheduler use the **direct** URL.
- `DB_DISABLE_PREPARED_CACHE=true` is the safe default. The smoke test tells you whether the cache works on your pooler; if it passes, you may set it to `false`.

## 7. Verify
```bash
make migrate     # alembic upgrade head on DATABASE_URL_DIRECT (reads the repo-root .env)
make smoke       # runs backend/scripts/neon_smoke_test.py
```
All critical checks must pass. The script checks that `ajo_app` does not bypass RLS, tenant context does not leak between pooled transactions, advisory transaction locks work, RLS isolates tenants (rolled back, no data kept), and prints latency. **Run the smoke test on the Frankfurt VPS too**: that is the latency that matters. Add the real numbers to the log below.

## 8. Backups and restore
- Check what restore window and instant-restore options your plan gives, and write them below.
- Nightly encrypted `pg_dump` to storage outside Neon and the VPS: `ops/backup/pg_backup.sh`. Use a `pg_dump` client version at least as new as the Neon server.
- Rehearse both restore routes monthly (`ops/runbooks/restore.md`).

## 9. Things to check on your plan (not verified here)
- Restore window and price of point-in-time recovery.
- IP allow-listing to the VPS address (which plans include it).
- Branch count and storage limits; compute-hours alert in billing.
- Neon Postgres major version offered, and a matching `pg_dump` on the VPS.

## 10. Learning log (fill in as you go)
| Date | Experiment | What I saw | Decision |
|---|---|---|---|
|  | Smoke test from laptop vs Frankfurt VPS (p50/p95) |  |  |
|  | First query after 10 minutes idle (scale-to-zero on staging) |  |  |
|  | Create branch from main, change data, reset branch to parent |  |  |
|  | Restore main to a point in time into a new branch |  |  |
|  | Prepared cache on pooled connection (DB_DISABLE_PREPARED_CACHE=false) |  |  |
|  | Compute usage after a day of staging traffic (cost estimate) |  |  |
