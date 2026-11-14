# Share request validation and register progress

STATE: Steps 1–3 complete; starting step 4. Steps 5–6 remain.

## Checklist

- [x] 1. Read the share purchase service, tests, the ledger balance code, and an existing list page for patterns. Write the plan into this file.
- [x] 2. A backend: tests first (over-savings blocked at request, exactly equal allowed, zero/negative blocked, approval re-check still works), then the fix.
- [x] 3. A frontend: over-savings message, disabled submit, tests (`assert.match` for regex checks).
- [ ] 4. B backend: a register endpoint reading balances from the ledger; tests first, including role access and tenant isolation. If a migration is needed, it is raw SQL, next number after the newest, exactly one head, never edit a merged one; run `scripts/check_migrations.sh`.
- [ ] 5. B frontend: the page, nav entry, tests.
- [ ] 6. Docs: update `docs/decisions.md` (request-time check, share register rules). Final `make check-fast`, `make fmt`, push.

## Plan

1. **Backend request guard:** In `request_purchase`, keep the existing active-member and member-row lock checks, then read the current `member_savings` ledger balance using the same helper/query semantics as `approve_purchase`. Reject non-positive counts and totals above available savings with an explicit client validation error (4xx); allow an exact match. Keep the approval-time recheck unchanged. Focused regression tests cover those cases.
2. **Request form:** Retain the selected member’s current savings display, obtain the current cooperative share price from an existing read surface, compute the requested total in integer kobo, show the same insufficient-savings message immediately, and disable submit for invalid/non-positive/over-balance requests. Add focused UI tests using `assert.match` where regex checks are needed.
3. **Share register API:** Add a paginated/searchable read projection based on tenant-scoped `member_shares` ledger journal lines, including holdings, current-price value, last purchase date, and cooperative totals. Enforce admin, treasurer, officer, auditor, and committee access in the service/API; keep every query inside `transaction(current.cooperative_id)` and explicitly constrain cooperative IDs. Apply money-view name rules and `Unnamed user`/`Unknown user` fallbacks. Add role and cross-tenant isolation tests. Reuse existing tables unless inspection demonstrates a migration is necessary.
4. **Share register UI:** Add the read-only page, search and pagination following the existing disbursement list conventions, and add its role-gated navigation entry. Add focused page/navigation tests; no write controls or notification changes.
5. **Documentation and verification:** Record the request-time guard and register semantics in `docs/decisions.md`. Keep `ACTION_SPECS` and notification CHECK kinds unchanged. Run `make fmt` before each commit and the permitted `make check-fast` checks; do not use Docker or run the full backend suite. Commit and push each completed checklist step and update this file and the draft PR checklist/state after each push.

## Initial findings

- `backend/app/domains/share_purchases/service.py` now reads the current `member_savings` balance through a helper shared with approval. Request-time failures use `UnprocessableEntity` (422), and approval retains its conflict/recheck behavior. `backend/tests/integration/test_share_purchases.py` covers over-balance, exact equality, zero/negative counts, and a savings decrease before approval.
- The share-purchase list API now returns `current_unit_price_naira`; the form uses it for an integer-kobo estimate, displays the server-matching error immediately when over savings, and disables submission unless the request is valid.
- `frontend/src/pages/SharePurchasesPage.tsx` retains its savings display and request/approval behaviors; `frontend/src/sharePurchasePolicy.test.mjs` covers exact equality, over-balance, invalid counts, and the warning/disabled-submit affordances.
- `backend/app/domains/members/reads.py` reads balances from journal lines and `accounts`; the shares account code is `member_shares`.
- `frontend/src/pages/DisbursementsPage.tsx` provides the existing debounced member search and paginated-list patterns.
- No `feat/share-register` branch or pull request existed at task start. Draft PR #75 was created after the required initial branch push.

## Verification

`node --test src/sharePurchasePolicy.test.mjs` passed (3 tests). The focused backend tests were authored before the service change but not run: `AGENTS.md` permanent run mode prohibits pytest. `npm run typecheck` is blocked by missing `vite/client` types; `npm run format` is blocked because Prettier is not installed. `make fmt` and `make check-fast` are blocked because `backend/.venv/bin/ruff` is missing. Repository rules prohibit installing dependencies. No Docker command or full backend suite was run.
