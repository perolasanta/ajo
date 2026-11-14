# Share request validation and register progress

STATE: Step 1 complete; steps 2–6 remain.

## Checklist

- [x] 1. Read the share purchase service, tests, the ledger balance code, and an existing list page for patterns. Write the plan into this file.
- [ ] 2. A backend: tests first (over-savings blocked at request, exactly equal allowed, zero/negative blocked, approval re-check still works), then the fix.
- [ ] 3. A frontend: over-savings message, disabled submit, tests (`assert.match` for regex checks).
- [ ] 4. B backend: a register endpoint reading balances from the ledger; tests first, including role access and tenant isolation. If a migration is needed, use raw SQL, the next number after the newest, exactly one head, never edit a merged one; run `scripts/check_migrations.sh`.
- [ ] 5. B frontend: the page, nav entry, tests.
- [ ] 6. Docs: update `docs/decisions.md` (request-time check, share register rules). Final `make check-fast`, `make fmt`, push.

## Plan

1. **Backend request guard:** In `request_purchase`, keep the existing active-member and member-row lock checks, then read the current `member_savings` ledger balance using the same query/semantics as `approve_purchase`. Reject non-positive counts and totals above available savings with an explicit client validation error (4xx); allow an exact match. Keep the existing approval-time recheck unchanged. Add the four requested regression cases before changing service behavior.
2. **Request form:** Retain the selected member’s current savings display, obtain the current cooperative share price from an existing read surface, compute the requested total in integer kobo, show the same insufficient-savings message immediately, and disable submit for invalid/non-positive/over-balance requests. Add focused UI tests using `assert.match` where regex checks are needed.
3. **Share register API:** Add a paginated/searchable read projection based on tenant-scoped `member_shares` ledger journal lines, including holdings, current-price value, last purchase date, and cooperative totals. Enforce admin, treasurer, officer, auditor, and committee access in the service/API; keep every query inside `transaction(current.cooperative_id)` and explicitly constrain cooperative IDs. Apply money-view name rules and `Unnamed user`/`Unknown user` fallbacks. Add role and cross-tenant isolation tests. Reuse existing tables unless inspection demonstrates a migration is necessary.
4. **Share register UI:** Add the read-only page, search and pagination following the existing disbursement list conventions, and add its role-gated navigation entry. Add focused page/navigation tests; no write controls or notification changes.
5. **Documentation and verification:** Record the request-time guard and register semantics in `docs/decisions.md`. Keep `ACTION_SPECS` and notification CHECK kinds unchanged. Run `make fmt` before each commit and the permitted `make check-fast` checks; do not use Docker or run the full backend suite. Commit and push each completed checklist step and update this file and the draft PR checklist/state after each push.

## Initial findings

- `backend/app/domains/share_purchases/service.py` checks only `shares_count > 0` at request time; approval already locks the member, reads the current `member_savings` balance from ledger lines, and rejects insufficient savings before posting.
- `backend/tests/unit/test_share_purchase_contracts.py` contains static contract coverage for the share API, approval ledger entry, and unchanged approval notification path.
- `backend/app/domains/members/reads.py` reads balances from journal lines and `accounts`; the shares account code is `member_shares`.
- `frontend/src/pages/SharePurchasesPage.tsx` already fetches/displays selected-member savings and submits positive integer share counts, but it does not compare request cost with savings.
- `frontend/src/pages/DisbursementsPage.tsx` provides the existing debounced member search and paginated-list patterns.
- No `feat/share-register` branch or pull request existed at task start. Draft PR #75 was created after the required initial branch push.

## Verification

`make fmt` was attempted but could not run because `backend/.venv/bin/ruff` is absent; no global Ruff module is installed. `make check-fast` was attempted and stopped at its first check for the same missing executable. Repository rules prohibit installing dependencies, so neither command could complete locally. No Docker command or backend test suite was run.
