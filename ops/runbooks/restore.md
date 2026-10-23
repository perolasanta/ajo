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
4. Re-run `scripts/neon_roles.sql` if roles are missing, then `alembic current`.
5. Verify ledger balances and audit hash chain (Phase 1+), then switch the app's env vars.

Log each drill: date, route, time taken, result, problems.
