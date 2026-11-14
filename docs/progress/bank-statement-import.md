# Bank statement import progress

STATE: Item 1 in progress; branch initialized from latest main and draft PR checkpoint pending.

## Checklist

- [ ] 1. Read payments inbox, payment confirmation, ledger, payroll CSV import, and list-page patterns; write implementation plan.
- [ ] 2. Add the bank statement migration with tables, constraints, unique indexes, and RLS.
- [ ] 3. Add parser tests and implementation with preview/import endpoint and duplicate handling.
- [ ] 4. Add matcher tests and implementation with list/get endpoints and tenant isolation.
- [ ] 5. Add accept/ignore tests and implementation using existing payment confirmation.
- [ ] 6. Add the Bank statements frontend page, navigation, and frontend tests.
- [ ] 7. Update docs, run final checks, push, and keep the PR draft.

STOPPED AT: item 1, next action: complete the required code-pattern reading and plan, then commit and push item 1.

## Plan

- Inspect existing payment confirmation and action eligibility so statement acceptance delegates to the established maker-checker path.
- Reuse payroll CSV parsing and upload-limit patterns without storing raw uploads or posting ledger entries.
- Add tenant-scoped raw-SQL migration objects and parser/matcher services with integer-kobo amounts, deterministic fingerprints, and row-level statuses.
- Add targeted tests before each backend slice, then a role-gated frontend page and decision documentation.
