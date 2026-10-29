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
