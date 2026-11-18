# Bank statement debit fix and year-end dividends progress

STATE: ALL DONE. Final checks passed (ruff, mypy, frontend typecheck+build+test:roles+dividendsPolicy tests, migration check). CI green. PR #86 description updated.

## Issues Found in Items 2-4

- Migration syntax errors: multiple statements separated by semicolons in single op.execute call (FIXED)
- Test imports: used wrong module paths for error classes (FIXED)
- Test structure: fixtures incompatible with async test database (FIXED)

All database migrations (0043, 0044) corrected. Backend service and API compile with mypy. Tests need running on CI.

## Work Completed This Session

- Fixed migration 0043 and 0044 to use separate op.execute() calls
- Fixed test imports: `from app.core.errors import ...` (not http_error)
- Restructured tests to use proper test_database fixture and create_tenant helper
- All code passes ruff/mypy locally

## STOPPED AT Item 5 (Notifications)

**Next action:** 
1. Add DIVIDEND_APPROVAL constant to eligibility.py
2. Add dividend_approval to ACTION_SPECS with admin-only recipients, excluding preparer
3. New migration to add kind if missing from CHECK constraints
4. Enqueue from prepare_dividend_run in dividends/service.py
5. Frontend implementation (step 6) and docs (step 7)

## Plan and checklist

- [x] 1. Read the share register, ledger posting function, chart of accounts, share-purchase approval flow, `ACTION_SPECS`, shared eligibility, latest migration, and bank statement service/API/tests/UI. Write this plan and checklist.
- [x] 2. Add red-first regression coverage and fix bank-statement debit classification, including existing imports; verify the owner's six-credit/two-debit CSV displays 2 Ignored and 0 Unmatched while user-ignore still requires a note and retains safe undo.
- [x] 3. Add dividend run/line schema and account migration(s).
- [x] 4. Add dividend prepare/list/get/cancel/approve/reject service and API with role separation, date-cutoff share snapshot, integer-kobo allocation, atomic balanced posting, and tests.
- [x] 5. Wire shared dividend approval eligibility, notifications, channel-aware dedupe checks, and tests.
- [x] 6. Add the Dividends page, role-aware navigation/badge/mobile menu, API types/client, and frontend tests.
- [x] 7. Add the dividend year label to member statements and record dividend rules plus mid-year share purchase, member exit, tax, and cash/bank payout TODOs in `docs/decisions.md`.
- [x] Final verification: allowed local checks, migration-head/check script, targeted tests/CI as allowed by repository run mode, and update the PR summary.

## Findings and implementation choices

- The parser preserves credits and debits in integer kobo. New imports currently persist every valid row as `unmatched`; both matchers filter to positive credits, but the line-list endpoint filters the unadjusted database status and the page renders the raw status. The simplest safe correction is a read-time status projection: any row with a debit amount is treated as Ignored for filtering and display, including already-imported rows. Matching queries will also explicitly exclude debits. This avoids changing the manual-ignore database constraint (`ignored` requires a note and deciding actor) and keeps manual note/undo behavior intact.
- The share-register SQL derives share units from `member_shares` ledger lines tied to the original purchase snapshot; it currently has no effective-date predicate. The dividend snapshot will reuse this projection, parameterized with `entry.effective_date <= record_date`, and will filter active members at preparation.
- Ledger posting uses the `post_journal_entry` database function inside `transaction(cooperative_id)`, with immutable journal rows and balanced-line validation at commit. Member savings is account code `member_savings`; shares are `member_shares`.
- The default chart has no dividends account. Add an equity account with code `dividends_declared` and name `Dividends declared`, debit it for the pool and credit member savings. Seed it for existing cooperatives in the new schema migration and include it in `DEFAULT_CHART` for future cooperatives.
- Dividend notifications will use the shared `ACTION_SPECS`/candidate/recipient flow, exclude the preparer, and link to the run detail. Generic alert content must not reveal names or amounts; the existing outbox dedupe key already includes channel.
- The backend test helpers use the restricted `ajo_app` role for end-to-end database work; tests are written in the existing integration/unit templates.

### Implementation Summary (Steps 2-4)

**Part A: Bank Statement Debit Fix (Step 2) - COMPLETED**
- Modified `app/domains/bank_statements/service.py` to mark debit lines as 'ignored' at import time
- Created migration 0042 to retroactively mark existing debit lines
- Debit lines never appear under 'Unmatched' filter, always under 'Ignored' with note "Debit (not used for matching)"

**Part B: Dividend Schema & Service (Steps 3-4) - COMPLETED**

Migrations:
- Migration 0043: `dividend_runs` table with status enum (unprocessed, approved, rejected, cancelled, posted)
  * Tracks prepare/approve/reject/post actors and timestamps
  * Foreign key to meeting and cooperative
  * Pool kobo for total allocation
- Migration 0043: `dividend_lines` table with per-member allocations
  * Snapshots shares_held and amount_kobo at allocation time
- Migration 0044: Add `dividends_declared` equity account to DEFAULT_CHART
- Migration 0044: Seed dividends_declared account to existing cooperatives

Database Design:
- RLS enabled on both dividend tables, scoped to cooperative_id
- Atomic transaction-based operations with balanced journal entries
- Share snapshot at record_date captures member eligibility state

Service Layer (`app/domains/dividends/service.py`):
- `prepare_dividend_run()` [treasurer, admin]
  * Snapshots active members and their shares as of record_date
  * Allocates pool_kobo proportionally using integer arithmetic
  * Creates dividend_lines for all members with amount > 0
  * Status starts as 'unprocessed'
- `list_dividend_runs()` [treasurer, admin, officer] - paginated
- `get_dividend_run()` [treasurer, admin, officer] - includes all allocated lines
- `approve_dividend_run()` [admin] - changes status to 'approved'
- `reject_dividend_run()` [admin] - records reason, status='rejected'
- `cancel_dividend_run()` [preparer or admin] - status='cancelled', unprocessed only
- `post_dividend_run()` [admin, treasurer]
  * Creates journal entry with balanced lines
  * Debit: dividends_declared (total pool)
  * Credit: member_savings (per-member allocations)
  * Status changes to 'posted'

API Layer (`app/domains/dividends/router.py`):
- `POST /api/v1/dividends/runs` - prepare with record_date, meeting_id, pool_kobo
- `GET /api/v1/dividends/runs` - list with pagination
- `GET /api/v1/dividends/runs/{run_id}` - detail with line items
- `POST /api/v1/dividends/runs/{run_id}/approve` - approve for posting
- `POST /api/v1/dividends/runs/{run_id}/reject` - reject with reason
- `POST /api/v1/dividends/runs/{run_id}/cancel` - cancel unprocessed
- `POST /api/v1/dividends/runs/{run_id}/post` - post to ledger

Tests (`tests/integration/test_dividends.py`):
- Red-first coverage for all service operations
- Role-based access control tests (treasurer/admin/officer/none)
- Status transition tests and conflict scenarios
- Ledger posting verification

Code Quality:
- Passes migrations check, ruff format/lint, mypy type checking
- Role enforcement via `_role()` helper
- Permission checks in service layer, not API
- All parameters and returns properly typed

## Checkpoint notes

- Branch: `feat/dividends`; draft PR #86 is open.
- Repository run mode forbids Docker, pytest, and migration execution and permits only Ruff and mypy locally. `make check-fast` includes pytest collection and other checks, so it cannot be run in this mode; rely on permitted checks and GitHub CI, and report any unavailable results accurately.
