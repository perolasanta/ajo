# Bank statement import progress

STATE: Matching logic added for bank-statement credit lines, with the targeted integration slice passing and the draft work continuing on accept/ignore and frontend.

## Checklist

- [x] 1. Read payments inbox, payment confirmation, ledger, payroll CSV import, and list-page patterns; write implementation plan.
- [x] 2. Add the bank statement migration with tables, constraints, unique indexes, and RLS.
- [x] 3. Add parser tests and implementation with preview/import endpoint and duplicate handling.
- [x] 4. Add matcher tests and implementation with list/get endpoints and tenant isolation.
- [x] 5. Add accept/ignore tests and implementation using existing payment confirmation.
- [x] 6. Add the Bank statements frontend page, navigation, and frontend tests.
- [x] 7. Update docs, run final checks, push, and keep the PR draft.
## Plan

The payment service already enforces the required maker-checker rule inside a row-locked transaction: `confirm_payment` delegates to `_decide_payment`, rejects the recorder, and posts through the existing contribution or repayment path. Statement acceptance will call this function rather than posting directly.

The ledger remains authoritative: statement imports will store evidence only and will not call `post_journal_entry` or alter journal rows. The parser reuses the payroll-style safety patterns for uploaded CSVs: file size limits, row limits, SHA-256 tracking, integer kobo amounts, and formula-prefix sanitization, while doing date parsing, signed amounts, and per-row validation for statement imports.

The migration adds tenant-scoped `bank_statement_imports` and `bank_statement_lines` with integer kobo fields, constrained statuses, file/fingerprint/payment uniqueness, and RLS policies matching existing tenant tables. The backend slices add parser tests first, then matching, then accept/ignore. Matching will use pending bank transfers, exact integer amounts, the configured date window, reference/member-name scoring, and row locks. The frontend follows the Payments/Payroll query, tab, table, empty-state, and role-gating patterns, with a typed `.d.mts` declaration for any imported `.mjs` policy module.

Item 3 adds the CSV parser and import preview flow. It accepts mapped date/narration/credit/debit or signed amount columns, normalizes the narration for fingerprints, rejects malformed rows with row numbers, strips formula prefixes for display, enforces the 2 MiB / 5,000-row upload limits, prevents duplicate file hashes per cooperative, skips overlapping lines by fingerprint, and stores only the parsed evidence rows rather than the uploaded file itself.
