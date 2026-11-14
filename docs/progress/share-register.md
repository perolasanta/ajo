# Share request validation and register progress

STATE: Steps 1–5 complete; starting step 6. Step 6 remains.

## Checklist

- [x] 1. Read the share purchase service, tests, the ledger balance code, and an existing list page for patterns. Write the plan into this file.
- [x] 2. A backend: tests first (over-savings blocked at request, exactly equal allowed, zero/negative blocked, approval re-check still works), then the fix.
- [x] 3. A frontend: over-savings message, disabled submit, tests (`assert.match` for regex checks).
- [x] 4. B backend: a register endpoint reading balances from the ledger; tests first, including role access and tenant isolation. No migration was needed.
- [x] 5. B frontend: the read-only page, role-gated navigation, search/pagination, and tests.
- [ ] 6. Docs: update `docs/decisions.md` (request-time check, share register rules). Final `make check-fast`, `make fmt`, push.

## Plan

1. **Backend request guard:** In `request_purchase`, keep the existing active-member and member-row lock checks, then read the current `member_savings` ledger balance using the same helper/query semantics as `approve_purchase`. Reject non-positive counts and totals above available savings with an explicit client validation error (4xx); allow an exact match. Keep the approval-time recheck unchanged. Focused regression tests cover those cases.
2. **Request form:** Retain the selected member’s current savings display, obtain the current cooperative share price from an existing read surface, compute the requested total in integer kobo, show the same insufficient-savings message immediately, and disable submit for invalid/non-positive/over-balance requests. Add focused UI tests using `assert.match` where regex checks are needed.
3. **Share register API:** Add a paginated/searchable read projection based on tenant-scoped `member_shares` ledger journal lines, including holdings, current-price value, last purchase date, and cooperative totals. Enforce admin, treasurer, officer, auditor, and committee access in the service/API; keep every query inside `transaction(current.cooperative_id)` and explicitly constrain cooperative IDs. Apply money-view name rules and `Unnamed user`/`Unknown user` fallbacks. Add role and cross-tenant isolation tests. Reuse existing tables unless inspection demonstrates a migration is necessary.
4. **Share register UI:** Add the read-only page, search and pagination following the existing disbursement list conventions, and add its role-gated navigation entry. Add focused page/navigation tests; no write controls or notification changes.
5. **Documentation and verification:** Record the request-time guard and register semantics in `docs/decisions.md`. Keep `ACTION_SPECS` and notification CHECK kinds unchanged. Run `make fmt` before each commit and the permitted `make check-fast` checks; do not use Docker or run the full backend suite. Commit and push each completed checklist step and update this file and the draft PR checklist/state after each push.

## Implementation summary

- Request and approval share a ledger-balance helper; request-time failures use `UnprocessableEntity` (422), while approval retains its balance recheck and conflict behavior. Regression tests cover over-balance, exact equality, non-positive counts, and savings changing before approval.
- The request form reads the current share price from the purchase-list response, calculates an integer-kobo estimate, displays the matching insufficient-savings message immediately, and disables invalid submissions.
- `GET /share-register` returns ledger-derived holdings, current-price values, last purchase dates, cooperative totals, and paginated name search. Its read service permits admin/treasurer/officer/auditor/committee, uses tenant transaction context and explicit cooperative filters, handles purchase reversals through the originating price snapshot, and applies `Unnamed user`/`Unknown user` fallbacks. No migration or notification-kind change was needed.
- The read-only page shows total shares, members holding shares, total current value, current price, shares per member, share value, and last purchase date. It has debounced member-name search and pagination. Navigation and protected-route visibility match the backend role set; the page adds no write controls.
- Tests authored: backend request behavior, register role/contract/isolation behavior, and frontend request-policy, page-contract, and navigation-role behavior.

## Verification

`node --test src/sharePurchasePolicy.test.mjs` passed earlier (3 tests). Register/backend test files were authored but not run: `AGENTS.md` permanent run mode prohibits pytest and limits checks. `npm run typecheck` is blocked by missing `vite/client` types; `npm run format` is blocked because Prettier is unavailable. `make fmt` and `make check-fast` are blocked because `backend/.venv/bin/ruff` is missing. No dependencies were installed, no Docker command or migration was run, and no full backend suite was run.
