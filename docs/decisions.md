# Ajo Product Decisions

**Status:** Owner-approved

**Approved:** 2026-10-29

This document is authoritative for the decisions listed here and supersedes any conflicting or older statement in `docs/context.md` or other project documentation on these topics. It records product decisions; it does not itself authorize implementation. If a future task requires a decision not stated here, stop and ask the product owner before proceeding.

## 1. Member data access

Enforce these permissions server-side; hiding fields or navigation in the user interface is not sufficient.

| Role | Permitted member data and actions |
|---|---|
| **admin** | Full member read and staff management. Must not post financial transactions. |
| **treasurer** | Member name, balances, and bank-account last four digits. No phone number or email. |
| **officer** | Full member read for onboarding and follow-up. |
| **committee** | Member name and loan-relevant summary only. No contact, bank-account, or salary details. |
| **auditor** | Read-only access. Contact and bank details must be masked; no financial posting. |

## 2. Staff role changes

A role change must be proposed by one cooperative admin and approved by a different cooperative admin. The proposer cannot approve their own change. The last remaining admin must never be demoted; reject any change that would leave the cooperative without an admin.

## 3. Disputed or paused loans and repayments

Loans that are disputed or marked deduction-paused must be excluded from automated payroll deductions and automated payroll posting. Repayment on such a loan is allowed only as a manual repayment posted by a treasurer, with a reason recorded in the audit/transaction record.

## 4. Savings backing for loans

Savings backing is configured per cooperative in `cooperative_policy_versions.settings.savings_backing_bps` as an integer from 0 through 10,000; an absent key means 0 bps. At loan approval, snapshot the effective bps and computed amount on the approval record. Compute the amount in integer kobo as `max(min_savings_kobo, ceil(principal_kobo * savings_backing_bps / 10000))`, rounding any fractional kobo up. At disbursement, lock that snapshotted amount; later policy changes must not alter the stored values for an already-approved loan. Release the lock only on full repayment or write-off.

## 5. Borrower and beneficiary confirmation

When borrower and beneficiary differ, disbursement requires both (a) the borrower's confirmation of the final approved terms and (b) a separate confirmation from the beneficiary. Neither confirmation substitutes for the other. When borrower and beneficiary are the same member, the borrower's final-terms confirmation is required.

## 6. Payroll posting and sign-off

Post each payroll deduction component—loan repayment, savings, and dues—to its own ledger account as part of one atomic transaction. A payroll run may be signed off only after it is in `posted` status.

## 7. Idempotency

Bind every idempotency key to a canonical representation of its request payload. Reusing a key with an identical payload returns the original result. Reusing a key with a different payload returns HTTP `409 Conflict` and must not change financial records.

## 8. Member confirmation OTP

For each consent, permit at most three OTP sends per hour and at most five verification attempts in total. Resending does not reset the verification-attempt count. After the fifth unsuccessful attempt, lock the consent; an officer must reissue it before another confirmation attempt is possible.

Keep delivery provider-neutral until procurement. Implement delivery behind a provider interface, and keep provider credentials out of source code.

## 9. Loan term changes

Revalidate loan amount, tenor, and rate against the applicable loan-product limits both when terms are edited and when the loan is approved. Reject a change or approval if any term is outside those limits.
