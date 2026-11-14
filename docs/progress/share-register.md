# Share request validation and register progress

STATE: Steps 1–5 complete; step 6 documentation updated. Final verification is blocked on missing tooling, so step 6 remains open for owner checks.

STOPPED AT: backend targeted reproduction, next action: run the four failing backend tests and existing share-purchase tests with Docker/Postgres and the backend toolchain available, then run ruff, mypy, `make check-fast`, and `make fmt`.

## Checklist

- [x] 1. Read the share purchase service, tests, the ledger balance code, and an existing list page for patterns. Write the plan into this file.
- [x] 2. A backend: tests first (over-savings blocked at request, exactly equal allowed, zero/negative blocked, approval re-check still works), then the fix.
- [x] 3. A frontend: over-savings message, disabled submit, tests (`assert.match` for regex checks).
- [x] 4. B backend: a register endpoint reading balances from the ledger; tests first, including role access and tenant isolation. No migration was needed.
- [x] 5. B frontend: the read-only page, role-gated navigation, search/pagination, and tests.
- [ ] 6. Docs updated in `docs/decisions.md`. Owner must run the required checks and confirm before this step can be closed.

## Implementation summary

- Request and approval share a ledger-balance helper; request-time failures use `UnprocessableEntity` (422), while approval retains its balance recheck and conflict behavior. Regression tests cover over-balance, exact equality, non-positive counts, and savings changing before approval.
- The request form reads the current share price from the purchase-list response, calculates an integer-kobo estimate, displays the matching insufficient-savings message immediately, and disables invalid submissions.
- `GET /share-register` returns ledger-derived holdings, current-price values, last purchase dates, cooperative totals, and paginated name search. Its read service permits admin/treasurer/officer/auditor/committee, uses tenant transaction context and explicit cooperative filters, handles purchase reversals through the originating price snapshot, and applies `Unnamed user`/`Unknown user` fallbacks. No migration or notification-kind change was needed.
- The read-only page shows total shares, members holding shares, total current value, current price, shares per member, share value, and last purchase date. It has debounced member-name search and pagination. Navigation and protected-route visibility match the backend role set; the page adds no write controls.
- `docs/decisions.md` section 15 now records the request-time guard and register access, ledger, valuation, date, search, pagination, total, and name-label rules.
- Draft PR #75 remains open. Steps 1–5 are pushed; step 6 is committed after the final checkpoint.

## Verification

The frontend `npm run test:roles` and `node --test` checks passed (112 and 144 tests respectively). Typecheck/build are blocked by the missing `vite/client` dependency. The four failing backend tests and existing share-purchase tests could not run because Docker and pytest are unavailable; `make test-db-up`/`make test-db-down` fail with `docker: not found`. `make check-fast`/`make fmt` are blocked because `backend/.venv/bin/ruff` is missing; direct Ruff and mypy are also unavailable. No dependencies or migrations were run, and no full backend suite was run. Step 6 remains open pending owner-run checks.
