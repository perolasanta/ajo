# Ajo Product Decisions

**Status:** Owner-approved

**Approved:** 2026-10-29

This document is authoritative for the decisions listed here and supersedes any conflicting or older statement in `docs/context.md` or other project documentation on these topics. It records product decisions; it does not itself authorize implementation. If a future task requires a decision not stated here, stop and ask the product owner before proceeding.

## 1. Member data access

Enforce these permissions server-side; hiding fields or navigation in the user interface is not sufficient.

| Role | Permitted member data and actions |
|---|---|
| **admin** | Full member read and staff management. Must not post financial transactions. Full bank-account numbers are never returned by member endpoints. |
| **treasurer** | `GET /members`: `id`, `member_no`, `full_name`. `GET /members/{member_id}`: those fields, balances (`savings_kobo`, `shares_kobo`, `dues_paid_kobo`, `locked_kobo`, `available_kobo`), and bank-account `bank_code` and `last4` only. No contacts, account names, `staff_no`, or employer; none are needed in current treasurer workflows. |
| **officer** | Full member read for onboarding and follow-up, limited to fields currently returned by member endpoints. Full bank-account numbers are never returned. |
| **committee** | `id`, `member_no`, and `full_name` only on both member endpoints. No contact, bank-account, salary, or balance data. **Committee loan-summary TODO:** define the fields before implementing committee loan review. |
| **auditor** | Read-only. `GET /members`: `id`, `member_no`, `full_name`, `status`, `joined_on`. `GET /members/{member_id}`: those fields plus balances (`savings_kobo`, `shares_kobo`, `dues_paid_kobo`, `locked_kobo`, `available_kobo`). Omit contacts, `staff_no`, employer, bank accounts, and salary fields; no financial posting. |

## 1b. Payroll access and reporting

| Payroll operation | Allowed role(s) |
|---|---|
| `POST /payroll/runs` | Officer and treasurer |
| `GET /payroll/runs/{run_id}/expected.csv` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/dry-run` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/import` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/post` | Treasurer only |
| `POST /payroll/profiles` | Admin only |
| `POST /payroll/runs/{run_id}/sign-off` | Admin only; the signer must differ from the poster, or the importer for a run posted before poster tracking |

Committee and auditor roles have no payroll access. **TODO:** read-only sign-off report for auditors.

`payroll_items.resolution_note` currently has no application write path. Any future writer must validate its length and keep personal and bank data out. Exported resolution notes are capped at 500 characters.

## 2. Staff role changes

A role-change proposal targets one user, one role, and one action (`add` or `remove`), and must include a reason. Approval adds or removes only that role; all other roles remain unchanged. A cooperative admin proposes the change, and a different cooperative admin approves it. The proposer cannot approve. The target of an `add` proposal also cannot approve it. A pending duplicate for the same target, role, and action is rejected.

At approval time, the approver must still be an active admin and the target must still exist. An `add` is stale if the target already holds that role; a `remove` is stale if the target no longer holds it. Stale proposals are rejected. Proposals expire seven days after creation and cannot be approved after expiry. The proposer may cancel a pending proposal; an admin may reject one. Every propose, approve, reject, cancel, and expire event is audit-logged with actor (null for system expiry), target, old role, and new role.

Removing the admin role is permitted only when at least one active admin will remain. Enforce this inside the approval transaction under a per-cooperative advisory lock so concurrent approvals cannot remove the last two admins. On an effective role change, revoke all of the target's active sessions. This flow does not change account status or deactivate users. The former immediate role-change endpoint is gated and returns an error directing clients to proposals.

A cooperative with only one active admin cannot use maker-checker role changes: proposal creation returns a clear conflict until a second admin exists. The current bootstrap CLI creates the initial admin, and there is no staff-list or staff-invite API. Until a second admin is provisioned operationally, role changes remain blocked. There is no self-approval or single-admin bypass in the application.

**Follow-up work:** define role-combination and separation-of-duties rules; add staff-list and staff-invite endpoints if needed.

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

## 1c. Loan read access

Enforce this matrix server-side with role-specific response schemas. Read endpoints are tenant-scoped through the existing transaction and RLS. New fields are omitted by default. No loan read endpoint returns phone, email, salary, staff number, full account numbers, consent tokens, token hashes, or OTP data.

| Role | Loan read access |
|---|---|
| **admin** | Paginated application summaries and details, approved current loan products, masked destination bank account (`bank_code`, `last4`), terms, timeline, consent statuses, guarantor names, and repayment schedule. |
| **officer** | Same read fields as admin. |
| **treasurer** | Same loan read fields as admin, including masked destination bank account. |
| **committee** | Conservative application summary/detail only: application id, member number, member name, product name, principal, tenor, annual rate, status, submitted date, decision, decision reason, decided date, consent type/status, guarantor count and guarantor names. No bank fields or member contact fields. |
| **auditor** | Same conservative loan summary/detail fields as committee. No bank fields or member contact fields. |
| **all other roles** | HTTP 403. |

Bank-account ids are returned by admin/officer/treasurer member detail responses only so the submit form can select an account; account numbers remain masked to last four digits.

**TODO:** define a fuller committee loan-review summary after real committee review needs are validated.
