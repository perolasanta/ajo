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

## 1a. Member contribution and withdrawal writes

Enforce these write permissions in the member service; API and UI visibility are not authorization. Admins are read-only for all four actions below. Committee and auditor have no access. A multi-role user acts only through a role permitted for the action; role combinations never bypass actor-ID separation.

| Action | Allowed role(s) |
|---|---|
| Record a contribution | Officer, treasurer |
| Request a withdrawal on a member's behalf | Officer, treasurer |
| Approve a withdrawal request | Treasurer only |
| Post the withdrawal ledger entry | Treasurer only, in the same operation as approval (not a separate stage) |

Contribution records capture an effective date, reference/note, and payment method. Withdrawal requests capture a reason; a rejection requires a reason visible to the requester. Pending-withdrawal badges and notifications are available only to treasurers other than the requester, using the shared action-eligibility rules. Withdrawal queues are readable by treasurer, officer, and admin; committee and auditor cannot access them. Officers may see pending requests but cannot approve or reject them; admins are read-only.

Action queues, badges, list filters, and notifications use the shared eligibility rules and expose the same actionable entity IDs. Email and Telegram alerts use subject `Action needed in <APP_NAME>: <action kind>`, one plain sentence naming the action kind, and a direct item link. They contain no names, amounts, or member data. The link requires login and the normal page/API permission checks. Menu and dashboard badges link to the corresponding “Needs my action” list. Request posters and other ineligible participants are excluded.

The person who requested a withdrawal can never approve or reject it, even when they hold multiple roles. Members cannot request withdrawals in the app yet. Do not add a separate posting stage now. Follow-up: withdrawal payout as a distinct step with a separate poster, possibly with committee approval.

## 1b. Payroll access and reporting

| Payroll operation | Allowed role(s) |
|---|---|
| `POST /payroll/runs` | Officer and treasurer |
| `GET /payroll/runs` | Officer, treasurer, and admin; read-only, limited to run totals, counts, actor IDs and name-only actor labels, and timestamps |
| `GET /payroll/runs/{run_id}` | Officer, treasurer, and admin; read-only, limited to persisted run totals, counts, actor IDs and name-only actor labels, timestamps, and persisted exception fields |
| `GET /payroll/runs/{run_id}/expected.csv` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/dry-run` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/import` | Officer and treasurer |
| `POST /payroll/runs/{run_id}/post` | Treasurer only |
| `POST /payroll/profiles` | Admin only |
| `GET /payroll/profiles` | Officer, treasurer, and admin; read-only, limited to profile ID, display name, configured column headers, and creation time |
| `POST /payroll/runs/{run_id}/sign-off` | Admin only; the signer must differ from the poster, or the importer for a run posted before poster tracking |
| `POST /payroll/runs/{run_id}/items/{item_id}/resolution` | Treasurer only; CSRF-protected; only for a non-matched item on a posted, not signed-off run |

Committee and auditor roles have no payroll access. **TODO:** read-only sign-off report for auditors.

Payroll profile display names are generated from the stored profile version; the response contains no other profile metadata.

Dry-run results are not persisted. They remain in the current page session only; users must re-run the dry-run after a reload. This avoids retaining raw uploaded rows that were never imported. Run detail reads persisted `payroll_items` exceptions only and does not include original row numbers, which are not stored.

Payroll uploads must be CSV (`.csv`, `text/csv`) and are limited to 5 MiB (5 × 1024 × 1024 bytes) and 50,000 data rows. The server enforces the byte limit while streaming the request, independent of `Content-Length`; oversized requests return 413, non-CSV media types return 415, and files exceeding the row limit return 422.

Each non-matched payroll item may receive exactly one trimmed 10–500-character resolution note; notes cannot be edited or deleted. Resolution writes require an `Idempotency-Key`: the same key and payload replay successfully, while reusing a key with a different payload conflicts. Store the author in `resolved_by` and the write time in `resolved_at`. Keep personal and bank data out of note text. Audit the actor, run, and item without including the note text; sign-off CSV exports retain the existing 500-character cap and formula-injection neutralization. A posted run awaits admin sign-off only after every non-matched item has a note. Enqueue sign-off action notifications for admins except the poster and importer, relying on the existing outbox deduplication key.

## 2. Staff role changes

A role-change proposal targets one user, one role, and one action (`add` or `remove`), and must include a reason. Approval adds or removes only that role; all other roles remain unchanged. A cooperative admin proposes the change, and a different cooperative admin approves it. The proposer cannot approve. The target of an `add` proposal also cannot approve it. A pending duplicate for the same target, role, and action is rejected.

At approval time, the approver must still be an active admin and the target must still exist. An `add` is stale if the target already holds that role; a `remove` is stale if the target no longer holds it. Stale proposals are rejected. Proposals expire seven days after creation and cannot be approved after expiry. The proposer may cancel a pending proposal; an admin may reject one. Every propose, approve, reject, cancel, and expire event is audit-logged with actor (null for system expiry), target, old role, and new role.

Removing the admin role is permitted only when at least one active admin will remain. Enforce this inside the approval transaction under a per-cooperative advisory lock so concurrent approvals cannot remove the last two admins. On an effective role change, revoke all of the target's active sessions. This flow does not change account status or deactivate users. The former immediate role-change endpoint is gated and returns an error directing clients to proposals.

A cooperative with only one active admin cannot use maker-checker role changes: proposal creation returns a clear conflict until a second admin exists. Until a second admin is provisioned operationally, role changes remain blocked. There is no self-approval or single-admin bypass in the application.

`GET /staff` is admin-only, paginated, tenant-scoped, and returns only user ID, email, optional full name, account status, roles, and creation time. `GET /role-change-proposals` is admin-only, paginated, tenant-scoped, and filters by `all`, `pending`, `approved`, `rejected`, `cancelled`, or `expired` (default `pending`). Its explicit response contains proposal/target/proposer IDs and emails, proposer and decider display labels, one role and action, the submitted reason, current status, and lifecycle timestamps/deciding admin ID; it never returns credentials, MFA material, or sessions. Pending proposals past their expiry time are presented as expired by the read projection without mutating the proposal.

**Follow-up work:** staff invitations/provisioning through the staff-management UI; define role-combination and separation-of-duties rules.

### 2a. Staff display names and actor labels

`users.full_name` is nullable. Existing staff without a name display their email only on Staff, audit, and role-change views where the role already has email access. Staff provisioning remains service/CLI-only; its existing create paths may accept an optional `--full-name`/`full_name`. Admins may edit or clear the name of an existing user from the Staff page; this does not add invitations or account creation to the UI.

Audit and role-change actor labels for their authorized readers use `Full Name (email)`, falling back to email when a name is absent. Disbursement and payroll actor labels show only the full name; an existing account without a name is `Unnamed user`, and a missing account is `Unknown user`. These responses retain the stored actor IDs and never expose email to roles without existing email access. Audit actor names are joined at read time; hash-chained `audit_logs` rows are never rewritten.

Menu action badges count only entity IDs returned by the shared action eligibility rules in sections their existing roles can open: treasurers see payroll posting, disbursement recording, disputed/paused manual repayments, and withdrawal approvals; admins see payroll sign-off (excluding the poster or legacy importer), disbursement authorization/confirmation, and eligible role approvals; committee users see loan decisions; multi-role users receive the union of their eligible counts, with self-actions excluded.

### 2b. Settings approval review

Pending cooperative policy and loan-product versions are reviewed in a dedicated Settings panel. The panel compares the proposal with the currently effective or superseded version, renders changed values in human units, and includes the WAT effective date, reason, proposer, and proposed time. Approve and reject controls exist only in this panel; rejection requires a reason and approval confirms the changed fields and effective date. A proposer sees an informational message and cannot approve or reject their own proposal.

Policy and product approvals are admin-only actions through the shared action-eligibility function. Proposers are excluded from candidates, badges, queues, email, and Telegram notifications. Settings action kinds are `policy_approval` and `product_approval`; alerts use the generic text `A settings change needs your approval` and a direct proposal link, without names or amounts. Settings history joins users at read time: authorized admin viewers see `Full Name (email)`, other authorized viewers see `Full Name`, with `Unnamed user` and `Unknown user` fallbacks. Stored proposal and audit rows are not rewritten.

### 2c. Payments

Manual member payments are recorded by an officer or treasurer and are tenant-scoped by the cooperative. A bank transfer waits for confirmation by a different officer or treasurer; the payment recorder can never confirm or reject their own transfer. Cash receipts are recorded by an officer or treasurer, and a different treasurer must confirm the bank deposit. The treasurer who confirms the deposit must differ from every receipt recorder in the batch; a deposit can include only cash receipts with status `received` on the same cooperative and cannot be dated before any included receipt. The cooperative policy's `cash_banking_days` sets the banking window (default two WAT calendar days); receipts older than that threshold are marked overdue in the needs-my-action queue and on the cash-deposit list.

Payments use the shared action-eligibility rules so the menu badge, action queue, and notification recipients all expose the same entity IDs. The payment confirmation alert reads `A payment needs your confirmation`; the cash banking alert reads `Cash needs to be banked`; both messages omit member names, amounts, and other identifiers. The recorder can never be eligible as the confirmer for the same payment or receipt set, regardless of multi-role membership.

An officer or treasurer may confirm or reject a pending bank transfer only when they did not record it; rejection requires a reason that is shown to the recorder. A cash deposit is a treasurer-only write, and the confirming treasurer must differ from every recorder in the selected receipt batch. The Payments view is available to officers, treasurers, admins, and auditors (admins and auditors are read-only); committee users have no access. The cash-awaiting-deposit list shows WAT calendar age and marks a receipt overdue only after its effective `cash_banking_days` window has elapsed. Cash-on-hand totals are sourced from the `cash_on_hand` ledger account and are reconciled against unbanked receipts grouped by recorder for treasurer, admin, and auditor views. Received or confirmed payments have printable receipts with a receipt number; the print view does not display bank account numbers.

The staff Payments page is available to officers, treasurers, admins, and auditors; committee users have no access. Officers and treasurers may record payments, while admins and auditors are read-only. The recording form accepts naira and converts it exactly to integer kobo, requires a bank reference for transfers, and records the received date. Loan repayments are selected from the chosen member's loans. Disputed or deduction-paused repayments follow the existing manual treasurer-only path and require a reason. The form retains its idempotency key and values for a retry of the same attempt, and clears only after a successful response.

**Follow-up work:** add Paystack sub-accounts and virtual account support; support member-initiated payments and exit fees once the product and payment routing model are approved.

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

Loan eligibility also reads `cooperative_policy_versions.settings.min_membership_months` (integer 0–600; absent means no check) and `guarantors_must_be_active` (boolean; absent means true). Both checks run at submission and approval using WAT calendar dates; guarantors must be same-cooperative, unique, and not the borrower. The exact membership error is `Member has been a member for N months; at least M required.` Approved loans retain their stored terms and schedules when later policy versions change.

## 1c. Loan read access

Enforce this matrix server-side with role-specific response schemas. Read endpoints are tenant-scoped through the existing transaction and RLS. New fields are omitted by default. No loan read endpoint returns phone, email, salary, staff number, full account numbers, consent tokens, token hashes, or OTP data.

| Role | Loan read access |
|---|---|
| **admin** | Paginated application summaries and details, approved current loan products, and destination details (`account_name`, `bank_code`, `last4`), plus terms, timeline, consent statuses, guarantor names, and repayment schedule. |
| **officer** | Same read fields as admin, including destination `account_name`, `bank_code`, and `last4`. |
| **treasurer** | Same loan read fields as admin, including destination `account_name`, `bank_code`, and `last4`. |
| **committee** | Conservative application summary/detail only: application id, member number, member name, product name, principal, tenor, annual rate, status, submitted date, decision, decision reason, decided date, consent type/status, guarantor count and guarantor names. No bank fields or member contact fields. |
| **auditor** | Same conservative loan summary/detail fields as committee. No bank fields or member contact fields. |
| **all other roles** | HTTP 403. |

For loan snapshots, destination details are shown only to admin, officer, and treasurer. Committee and auditor receive no destination details. Full account numbers remain excluded; raw UUIDs and internal `*_kobo`/`*_bps` fields are never presented.

Bank-account ids are returned by admin/officer/treasurer member detail responses only so the submit form can select an account; account numbers remain masked to last four digits.

**TODO:** define a fuller committee loan-review summary after real committee review needs are validated.

## 1d. Disbursement read access

`GET /disbursements`, `GET /disbursements/{id}`, and `GET /disbursements/ready-loans` are tenant-scoped. List reads support pagination, status filtering, and search by member name or number. New fields are omitted unless explicitly allowlisted.

| Role | Disbursement read access |
|---|---|
| **admin** | Paginated list, detail timeline, and ready-loan queue. May receive masked destination `bank_code` and `last4`. |
| **treasurer** | Same read fields as admin, including masked destination `bank_code` and `last4`. |
| **auditor** | Read-only list, detail timeline, and ready-loan queue. Destination bank fields are omitted. |
| **officer, committee, members, all other roles** | HTTP 403. |

Read projections include disbursement, loan and application ids; member number and name; amount in kobo; status; preparer, authorizer and paid confirmer user ids; transfer reference/date; failure and reversal reasons; and available workflow timestamps (`created_at`, `authorized_at`, `transfer_recorded_at`, `paid_at`, `failed_at`, `reversed_at`). Failure reason is read from the tenant-scoped audit record because it is not a disbursement-table column. No phone, email, salary, staff number, full bank-account number, token or hash is returned. These reads do not initiate or represent a bank transfer; money is transferred manually outside the application.

## 1e. Disbursement write access

Enforce this matrix in the disbursement service layer; API/UI visibility is not authorization. Every operation remains tenant-scoped and uses the existing transaction and row-lock behavior. Roles not listed as allowed receive HTTP 403.

| Disbursement write action | Allowed roles |
|---|---|
| Prepare disbursement | **treasurer** only |
| Authorize disbursement | **admin** only |
| Record manual bank transfer | **treasurer** only |
| Confirm paid and post ledger entry | **admin** only |
| Mark transfer failed | **treasurer**, **admin** |
| Reverse paid disbursement | **admin** only |
| Committee, officer, auditor, member, and all other roles | HTTP 403 on every disbursement write |

Enforce separation of duties against persisted actor ids while holding the existing disbursement row lock: the preparer (`prepared_by`) cannot authorize; the authorizer (`authorized_by`) cannot confirm paid. Existing checks also keep the authorizer and paid confirmer independent of the application submitter and loan approvers. A user holding multiple roles does not bypass actor-id separation. These write operations record a manually performed transfer; Ajo itself does not move money.

## 1f. Audit log read access

`GET /audit-events` is read-only, paginated, filterable by timestamp range, actor, action, and entity type, and tenant-scoped through the existing transaction and RLS context. Admins and auditors may read it; members and every other role receive HTTP 403. No audit write or delete path is added. The optional `GET /audit-events/verify` endpoint is also restricted to admins and auditors and reports whether the existing hash chain is valid or the first broken position.

The response allowlist is exact: `event_id`, `timestamp`, `actor_id`, `actor_role`, `action`, `entity_type`, `entity_id`, and `safe_summary`. `safe_summary` admits only five keys (`status`, `decision`, `old_role`, `new_role`, and `count`), and values are separately checked: status/decision/role values must match finite safe sets, and count must be a non-boolean integer from 0 to 1,000,000. Free text, unknown values and all other summary keys are omitted. Phone, email, salary, full account numbers, tokens, OTP data, passwords, MFA material, IP/device data, channel, reason, hash-chain fields, and arbitrary before/after summaries are intentionally left out because audit rows may contain sensitive or unbounded values. The existing append-only audit table and hash-chain writer are unchanged.

## 1g. Member detail tabs
The read-only member detail tabs use these defaults where the earlier member and loan decisions do not explicitly name a tab. Overview retains the existing member-detail allowlists. Statement is available to admin, officer, treasurer, and auditor; committee has no statement access. Loans are available to admin, officer, treasurer, committee, and auditor, but committee and auditor receive only the conservative loan-summary fields already allowed by section 1c, so outstanding balances and repayment-schedule fields are omitted for those roles. Activity is available only to admin and auditor and uses the existing audit projection and actor-name rules. Actions and channel binding remain placeholders labeled “Coming soon”.
Statement amounts and running balances are calculated from tenant-scoped journal lines; no typed balance is accepted, and no tab response includes a full bank-account number or encrypted account-number field. All tab reads are tenant-scoped and paginated.
## 10. Staff action notifications

The provider-neutral outbox may enqueue every action event in `ACTION_SPECS`: payroll posting and sign-off, payment confirmation, cash deposit, disbursement recording/authorization/confirmation, role-change approval, loan decision, disputed loan, withdrawal approval, policy approval, and product approval. Each real business action enqueues its event inside the same tenant transaction. Recipients use the same role and exclusion eligibility as menu action badges, including union-of-roles behavior; never notify the actor who posted, prepared, recorded, proposed, or otherwise participates in the item, and never notify the target of a role add. Messages contain only the section name, generic action sentence, and a direct link built from `PUBLIC_BASE_URL`. **No business data in messages**: names, amounts, member data, references, and other business details must not appear in the payload. Enqueue failures are isolated so notification enqueueing cannot block the business action. Delivery adapters remain provider-neutral and best-effort.

Cash receipts that exceed the policy `cash_banking_days` window still require a scheduler job to enqueue an overdue alert; the scheduler is intentionally not implemented in the alert-wiring slice and is a follow-up.


## 11. Telegram account linking
Telegram links are personal staff settings, not role-gated navigation. A completed-MFA session may create a ten-minute, single-use start token; only its SHA-256 hash is persisted. The URL-safe token carries a base64url cooperative UUID prefix so the webhook establishes tenant context before lookup. At most three unexpired unused tokens are permitted per user, with an additional hourly creation limit. RLS is forced on both new tables using the standard cooperative policy. A Telegram chat can be linked to only one user globally, and each user can link one chat. The webhook requires constant-time comparison of Telegram's secret-token header, only accepts private `/start <token>` updates, and reveals no failure detail. Audit summaries contain no chat ids or tokens. Alert delivery remains out of scope.

## 12. Telegram action-alert delivery
For each eligible staff recipient, enqueue one email row when `EMAIL_ENABLED` is true and one Telegram row when `TELEGRAM_ENABLED` is true and that user has a Telegram link. Recipient eligibility, role-union behavior and participant exclusions are shared across channels. Rows deduplicate independently by cooperative, recipient, channel, kind and entity so email cannot suppress Telegram. Telegram payloads contain exactly the section label and public link; chat ids are resolved from the tenant-scoped link at send time and never enter the outbox. Telegram 403 and chat-not-found responses dead-letter and remove only the current matching link; 429 responses retry after Telegram's `retry_after`. Delivery is best-effort through the existing outbox, so provider failures do not fail business actions. The worker registers Telegram only when enabled; disabled Telegram rows are dead-lettered with an explicit reason. `telegram-set-webhook` registers the public webhook URL and secret-token header without printing credentials.

### 12a. Email action-alert delivery

The email channel keeps the existing generic action sentence and direct item link, with plain-text and simple HTML alternatives; messages contain no names, amounts, or member data. `EMAIL_PROVIDER` defaults to `brevo_api`, preserving the existing Brevo HTTPS adapter unchanged; `smtp` selects the worker's generic SMTP adapter under the same `email` channel. SMTP requires host, port, username, password, and security at worker startup. Use STARTTLS or implicit TLS outside development; unencrypted SMTP is development-only. SMTP credentials stay in environment configuration and are never logged. SMTP 4xx, connection, and timeout failures retry through the existing outbox backoff; 5xx responses and permanently rejected recipients dead-letter with safe error codes, and logs mask recipient addresses.

## 13. Account settings and staff sessions

Every authenticated account may access `/account` independently of the role-gated business sections. The profile displays the current user's name, email and roles read-only; an administrator edits names from the Staff page. The page displays MFA status only and does not enroll MFA, disable it, or view/regenerate recovery codes. The legacy `/notifications` route redirects to `/account`, where the existing personal Telegram connect/disconnect controls live.

Changing a password requires the current password and an MFA-complete session, is CSRF-protected, and uses the existing Argon2id hash. A new password is 12–128 characters, differs from the current password and the user's email, and is rejected if it matches the small built-in common-password denylist. Failed current-password attempts share login's account lockout and HMAC-keyed rate-limit policy. On success, revoke every other session while retaining the current session. Audit the password-change event without recording password values or other credentials.

Session listing and revocation are self-service and tenant-scoped: a user may see and revoke only their own sessions. Session summaries contain a browser/device label derived from the user agent, creation time, last-seen time and a current-device flag; never return session tokens, raw user agents or full IP addresses. The account page may revoke one of the user's sessions or all other sessions.

MFA enrollment continues to return the one-time recovery-code list from its existing API response. The current login UI does not display that response to the user. This decision does not change enrollment or recovery-code behavior.

Payroll run detail displays the sign-off actor using the existing payroll name-only label rule: trimmed full name, `Unnamed user` for an existing unnamed user, and `Unknown user` when the user record is absent.

## 14. Cooperative settings screens

The staff Settings navigation is available only to admins and officers. Officers may propose policy and loan-product versions; only admins may approve or supersede, and existing service-layer actor-separation and immutability rules remain authoritative. Settings reads are tenant-scoped and restricted to admin/officer. The UI accepts naira, percentages, months and loan-to-savings multiples, converting them to integer kobo/basis points/x100 at the API boundary. Effective dates are entered and displayed as WAT calendar dates. Policy versions are not edited in place; loan product changes create a new version.

## 15. Share purchases from savings

Share purchases cost one naira per share by default. Each cooperative stores its integer naira unit price setting; requests snapshot that price and the computed integer-kobo amount. Purchases may be requested for any positive number of shares by an officer or treasurer, and only for an active member.

The only payment source in this flow is the member's withdrawable savings ledger balance. Approval rechecks that balance and, in the same transaction, posts a balanced journal entry debiting the member's savings account and crediting the member's shares account. Ajo records and enforces the approval; it never moves money. Shares balances and statements are always derived from journal lines.

Requests use maker-checker separation: the requester can never approve or reject their own request, and only a different treasurer may decide. Admin, committee, and auditor are read-only. Requests have `pending`, `approved`, `rejected`, and `cancelled` statuses; rejection requires a note, the requester may cancel while pending, and only one pending request per member is allowed.

**TODO:** salary-deduction-funded share purchases are intentionally out of scope. Define and approve that payment source, payroll interaction, and separation-of-duties rules before implementing it.
