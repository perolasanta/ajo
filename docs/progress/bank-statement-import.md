# Bank statement import progress

STATE: Item 1 complete; PR #76 is draft and item 2 is next.

## Checklist

- [x] 1. Read payments inbox, payment confirmation, ledger, payroll CSV import, and list-page patterns; write implementation plan.
- [ ] 2. Add the bank statement migration with tables, constraints, unique indexes, and RLS.
- [ ] 3. Add parser tests and implementation with preview/import endpoint and duplicate handling.
- [ ] 4. Add matcher tests and implementation with list/get endpoints and tenant isolation.
- [ ] 5. Add accept/ignore tests and implementation using existing payment confirmation.
- [ ] 6. Add the Bank statements frontend page, navigation, and frontend tests.
- [ ] 7. Update docs, run final checks, push, and keep the PR draft.

STOPPED AT: item 1, next action: add the migration for bank statement imports and lines, including constraints, unique indexes, and RLS.

## Plan

The payment service already enforces the required maker-checker rule inside a row-locked transaction: `confirm_payment` delegates to `_decide_payment`, rejects the recorder, and posts through the existing contribution or repayment path. Statement acceptance will call this function rather than posting directly.

The ledger remains authoritative: statement imports will store evidence only and will not call `post_journal_entry` or alter journal rows. The existing payroll upload uses streamed byte limits, CSV parsing, row limits, SHA-256 reporting, integer amounts, and formula-prefix sanitization; the new parser will reuse those safety patterns but support mapped columns, signed amounts, dates, and per-row errors.

The migration will add tenant-scoped `bank_statement_imports` and `bank_statement_lines` with integer kobo fields, constrained statuses, file/fingerprint/payment uniqueness, and RLS policies matching existing tenant tables. The backend slices will add tests first for parsing/import deduplication, then matching, then accept/ignore. Matching will use pending bank transfers, exact integer amounts, the configured date window, reference/member-name scoring, and row locks. The frontend will follow the Payments/Payroll query, tab, table, empty-state, and role-gating patterns, with a typed `.d.mts` declaration for any imported `.mjs` policy module.
