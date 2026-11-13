# Share purchase from savings — progress

- [x] 1. Read existing patterns and write the implementation plan
- [x] 2. Add the migration and shares ledger account
- [x] 3. Add backend service, API, and tests
- [x] 4. Add notifications and tests
- [x] 5. Add the frontend page, actions, navigation badge, and tests
- [x] 6. Add share purchases and shares balance to member overview and statement
- [ ] 7. Update decisions, run final checks, and confirm draft PR

## Plan

1. Reuse the payments inbox, withdrawal, ledger, action-spec, shared eligibility, RLS, migration, and member statement patterns.
2. Add a tenant-scoped share-purchase request table and compulsory-shares account through the next raw SQL migration.
3. Implement savings-only request and maker-checker approval/rejection/cancellation flows with atomic ledger posting and row locking.
4. Wire the shared action eligibility and generic notification outbox for treasurer approvals.
5. Add the staff Share purchases page, request/decision controls, navigation badge, and member statement presentation.
6. Document the rules and the salary-deduction follow-up.

## Existing patterns confirmed

- `members.service` uses `transaction(cooperative_id)`, cooperative-member row locks, integer kobo ledger sums, `post_journal_entry`, append-only audit logging, and safe action-notification enqueueing.
- `members.api` contains role-specific member response models, paginated queues, CSRF-protected write routes, and member statement routing.
- `notifications.eligibility` is the single source for `ACTION_SPECS`, candidate SQL, role-union filtering, participant exclusion, badges, queues, and recipients.
- `notifications.outbox` deduplicates independently by cooperative, recipient, channel, kind, and entity and builds generic direct-link payloads.
- Migration `0036` is the current single head; raw SQL migrations enable and force RLS with `app_current_cooperative_id()` and grant only the required app-role privileges.
- `ledger.chart.DEFAULT_CHART` already includes `member_shares`; the migration must ensure existing cooperatives have that account without duplicating it.

Checks: `make fmt`/`make check-fast` could not run because the repository's `backend/.venv/bin/ruff` is absent; no dependencies were installed per `AGENTS.md`.

Migration `0037_share_purchase_requests.py` is syntactically valid. `scripts/check_migrations.sh` could not read Alembic because the repository environment is not installed (`No module named alembic`).

Backend files and the contract tests pass Python compilation and `git diff --check`. Database-backed tests and the required `make fmt`/`make check-fast` remain unavailable because the repository virtualenv/dependencies are absent.

Notification migration `0038` keeps the action-kind allowlist aligned with `ACTION_SPECS`, preserves channel-aware deduplication, and the request path uses the shared eligibility/notification enqueue function.

Frontend navigation tests pass (`79` tests). `npm run format`/`npm run typecheck` could not run because `prettier` is absent from the repository environment; no dependencies were installed per `AGENTS.md`.

Member overview already sourced `shares_kobo` from the ledger; it now displays it explicitly, and the statement includes a ledger-derived `shares_balance_kobo` plus `share purchase` entries.
