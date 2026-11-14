# Share request validation and register progress

STATE: Steps 1–4 complete; starting step 5. Steps 5–6 remain.

## Checklist

- [x] 1. Read the share purchase service, tests, the ledger balance code, and an existing list page for patterns. Write the plan into this file.
- [x] 2. A backend: tests first (over-savings blocked at request, exactly equal allowed, zero/negative blocked, approval re-check still works), then the fix.
- [x] 3. A frontend: over-savings message, disabled submit, tests (`assert.match` for regex checks).
- [x] 4. B backend: a register endpoint reading balances from the ledger; tests first, including role access and tenant isolation. No migration was needed.
- [ ] 5. B frontend: the page, nav entry, tests.
- [ ] 6. Docs: update `docs/decisions.md` (request-time check, share register rules). Final `make check-fast`, `make fmt`, push.

## Plan

1. **Backend request guard:** In `request_purchase`, keep the existing active-member and member-row lock checks, then read the current `member_savings` ledger balance using the same helper/query semantics as `approve_purchase`. Reject non-positive counts and totals above available savings with an explicit client validation error (4xx); allow an exact match. Keep the approval-time recheck unchanged. Focused regression tests cover those cases.
2. **Request form:** Retain the selected member’s current savings display, obtain the current cooperative share price from an existing read surface, compute the requested total in integer kobo, show the same insufficient-savings message immediately, and disable submit for invalid/non-positive/over-balance requests. Add focused UI tests using `assert.match` where regex checks are needed.
3. **Share register API:** Add a paginated/searchable read projection based on tenant-scoped `member_shares` ledger journal lines, including holdings, current-price value, last purchase date, and cooperative totals. Enforce admin, treasurer, officer, auditor, and committee access in the service/API; keep every query inside `transaction(current.cooperative_id)` and explicitly constrain cooperative IDs. Apply money-view name rules and `Unnamed user`/`Unknown user` fallbacks. Add role and cross-tenant isolation tests. Reuse existing tables unless inspection demonstrates a migration is necessary.
4. **Share register UI:** Add the read-only page, search and pagination following the existing disbursement list conventions, and add its role-gated navigation entry. Add focused page/navigation tests; no write controls or notification changes.
5. **Documentation and verification:** Record the request-time guard and register semantics in `docs/decisions.md`. Keep `ACTION_SPECS` and notification CHECK kinds unchanged. Run `make fmt` before each commit and the permitted `make check-fast` checks; do not use Docker or run the full backend suite. Commit and push each completed checklist step and update this file and the draft PR checklist/state after each push.

## Initial findings

- Request and approval now use a shared ledger-balance helper; request-time failures use `UnprocessableEntity` (422), while approval retains its balance recheck and conflict behavior. Backend tests cover over-balance, exact equality, non-positive counts, and savings changing before approval.
- The request form uses `current_unit_price_naira` from the purchase-list response, calculates an integer-kobo estimate, displays the matching insufficient-savings message, and disables invalid submissions.
- `GET /share-register` returns member holdings derived from `member_shares` ledger lines, current-price values, last purchase dates, cooperative totals, and paginated name search. Its read service checks the allowed role set and uses `transaction(current.cooperative_id)` plus explicit cooperative filters. Ledger reversal entries map back to the original purchase snapshot for unit conversion. Names use only the name label with `Unnamed user`/`Unknown user` fallbacks. No schema migration or notification-kind change was needed.
- Backend tests include role allow/deny cases, projection contract checks, and an integration test for search, pagination, current-price valuation, last-purchase date, and cross-tenant isolation.
- No `feat/share-register` branch or pull request existed at task start. Draft PR #75 was created after the required initial branch push.

## Verification

`node --test src/sharePurchasePolicy.test.mjs` passed (3 tests). Focused backend tests were authored first but not run: `AGENTS.md` permanent run mode prohibits pytest. `npm run typecheck` is blocked by missing `vite/client` types; `npm run format` is blocked because Prettier is not installed. `make fmt` and `make check-fast` are blocked because `backend/.venv/bin/ruff` is missing. Repository rules prohibit installing dependencies. No Docker command or full backend suite was run.
