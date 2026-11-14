# Bank statement import progress

STATE: Item 2 complete; PR #76 is draft and item 3 is next.

## Checklist

- [x] 1. Read payments inbox, payment confirmation, ledger, payroll CSV import, and list-page patterns; write implementation plan.
- [x] 2. Add the bank statement migration with tables, constraints, unique indexes, and RLS.
- [ ] 3. Add parser tests and implementation with preview/import endpoint and duplicate handling.
- [ ] 4. Add matcher tests and implementation with list/get endpoints and tenant isolation.
- [ ] 5. Add accept/ignore tests and implementation using existing payment confirmation.
- [ ] 6. Add the Bank statements frontend page, navigation, and frontend tests.
- [ ] 7. Update docs, run final checks, push, and keep the PR draft.

STOPPED AT: item 2, next action: write parser tests for dates, amounts, signed values, bad rows, formula text, limits, duplicate files, and overlapping statements.

## Plan

The payment service already enforces the required maker-checker rule inside a row-locked transaction: `confirm_payment` delegates to `_decide_payment`, rejects the recorder, and posts through the existing contribution or repayment path. Statement acceptance will call this function rather than posting directly.

The ledger remains authoritative: statement imports will store evidence only and will not call `post_journal_entry` or alter journal rows. The existing payroll upload uses streamed byte limits, CSV parsing, row limits, SHA-256 reporting, integer amounts, and formula-prefix sanitization; the new parser will reuse those safety patterns but support mapped columns, signed amounts, dates, and per-row errors.

The migration will add tenant-scoped `bank_statement_imports` and `bank_statement_lines` with integer kobo fields, constrained statuses, file/fingerprint/payment uniqueness, and RLS policies matching existing tenant tables. The backend slices will add tests first for parsing/import deduplication, then matching, then accept/ignore. Matching will use pending bank transfers, exact integer amounts, the configured date window, reference/member-name scoring, and row locks. The frontend will follow the Payments/Payroll query, tab, table, empty-state, and role-gating patterns, with a typed `.d.mts` declaration for any imported `.mjs` policy module.

Item 2 adds migration `0039_bank_statement_imports.py`. It stores no uploaded file contents, uses integer kobo columns, constrains evidence-line statuses and decision fields, enforces cooperative-scoped file-hash and line-fingerprint uniqueness plus one matched line per payment, and applies forced tenant RLS to both tables. `scripts/check_migrations.sh` could not run because Alembic is unavailable; `make fmt` and `make check-fast` could not run because Ruff is unavailable.
