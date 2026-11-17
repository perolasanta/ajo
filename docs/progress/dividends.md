# Bank statement debit fix and year-end dividends progress

STATE: Item 1 complete; item 2 next.

## Plan and checklist

- [x] 1. Read the share register, ledger posting function, chart of accounts, share-purchase approval flow, `ACTION_SPECS`, shared eligibility, latest migration, and bank statement service/API/tests/UI. Write this plan and checklist.
- [ ] 2. Add red-first regression coverage and fix bank-statement debit classification, including existing imports; verify the owner's six-credit/two-debit CSV displays 2 Ignored and 0 Unmatched while user-ignore still requires a note and retains safe undo.
- [ ] 3. Add dividend run/line schema and account migration(s), followed by notification-kind migration and parity coverage.
- [ ] 4. Add dividend prepare/list/get/cancel/approve/reject service and API with role separation, date-cutoff share snapshot, integer-kobo allocation, atomic balanced posting, and tests.
- [ ] 5. Wire shared dividend approval eligibility, notifications, channel-aware dedupe checks, and tests.
- [ ] 6. Add the Dividends page, role-aware navigation/badge/mobile menu, API types/client, and frontend tests.
- [ ] 7. Add the dividend year label to member statements and record dividend rules plus mid-year share purchase, member exit, tax, and cash/bank payout TODOs in `docs/decisions.md`.
- [ ] Final verification: allowed local checks, migration-head/check script, targeted tests/CI as allowed by repository run mode, and update the PR summary.

## Findings and implementation choices

- The parser preserves credits and debits in integer kobo. New imports currently persist every valid row as `unmatched`; both matchers filter to positive credits, but the line-list endpoint filters the unadjusted database status and the page renders the raw status. The simplest safe correction is a read-time status projection: any row with a debit amount is treated as Ignored for filtering and display, including already-imported rows. Matching queries will also explicitly exclude debits. This avoids changing the manual-ignore database constraint (`ignored` requires a note and deciding actor) and keeps manual note/undo behavior intact.
- The share-register SQL derives share units from `member_shares` ledger lines tied to the original purchase snapshot; it currently has no effective-date predicate. The dividend snapshot will reuse this projection, parameterized with `entry.effective_date <= record_date`, and will filter active members at preparation.
- Ledger posting uses the `post_journal_entry` database function inside `transaction(cooperative_id)`, with immutable journal rows and balanced-line validation at commit. Member savings is account code `member_savings`; shares are `member_shares`.
- The default chart has no dividends account. Add an equity account with code `dividends_declared` and name `Dividends declared`, debit it for the pool and credit member savings. Seed it for existing cooperatives in the new schema migration and include it in `DEFAULT_CHART` for future cooperatives.
- Dividend notifications will use the shared `ACTION_SPECS`/candidate/recipient flow, exclude the preparer, and link to the run detail. Generic alert content must not reveal names or amounts; the existing outbox dedupe key already includes channel.
- The backend test helpers use the restricted `ajo_app` role for end-to-end database work; tests are written in the existing integration/unit templates.

## Checkpoint notes

- Branch: `feat/dividends`; draft PR #86 is open.
- Repository run mode forbids Docker, pytest, and migration execution and permits only Ruff and mypy locally. `make check-fast` includes pytest collection and other checks, so it cannot be run in this mode; rely on permitted checks and GitHub CI, and report any unavailable results accurately.
