# Implementation Plan — seed-demo CLI, notifications-dead CLI, supporting reads, 3 tests

All money is integer kobo (`int`). No floats. All state changes go through existing services inside
`app.core.db.transaction(cooperative_id)` (tenant role `ajo_app`, RLS via `SET LOCAL`). The seed never
uses the owner/direct connection and writes no raw SQL. Run mode (permanent): the coder runs **only**
`ruff` and `mypy`; the owner runs pytest / `make test` / `make migrate`.

Binding facts confirmed by reading the code: `tenant_directory` has **no RLS** (so `resolve_tenant_slug`
works before tenant context); `salary_records` and `bank_accounts` have RLS + tenant policies and are
append-only for `ajo_app` (SELECT/INSERT only); the default chart (created by `create_cooperative`) already
contains every account the disbursement/repayment/contribution services need (`bank`, `loans_receivable`,
`interest_income`, `dues_income`, `member_savings`, `member_shares`, `fee_income`, `unallocated_receipts`);
`notifications.status` already includes `'failed'` (the dead-letter state) — **no migration needed**.

---

## 1. Existing services found (exact signatures) — USE THESE, do not re-create

Policy (`app/domains/policies/service.py`)
- `async def propose_policy(current, values: dict[str, Any], reason: str) -> uuid.UUID` — roles officer/admin.
- `async def approve_policy(current, policy_id: uuid.UUID, reason: str) -> None` — role admin; **approver must differ from proposer**.
- `async def get_effective_policy(cooperative_id: uuid.UUID, on_date: date) -> dict[str, Any]` — read.

Loan product (`app/domains/policies/service.py`)
- `async def create_loan_product(current, product: dict[str, Any], on_date: date, reason: str) -> uuid.UUID` — roles officer/admin; validates `guarantors_required >= policy.guarantors_required` and `interest_method == policy.interest_method`.
- `async def approve_loan_product(current, product_id: uuid.UUID, on_date: date, reason: str) -> None` — role admin.

Application / decision (`app/domains/loans/service.py`)
- `async def submit_application(current, *, member_id, product_id, principal_kobo, tenor_months, beneficiary_member_id=None, destination_bank_account_id=None, guarantors=None, consent_delivery_observer: Callable[[str], None] | None = None) -> uuid.UUID` — roles officer/admin. Inserts `loan_applications` (status `awaiting_applicant_confirmation`), creates an `applicant_ack` `member_consents` row, creates a `notifications` outbox row, and calls `consent_delivery_observer(token.hex())` before commit.
- `async def run_eligibility(cooperative_id, application_id) -> dict[str, Any]` — read.
- `async def decide_application(current, application_id, decision: str, reason: str, consent_delivery_observer=None) -> uuid.UUID | None` — role **committee**, **must differ from submitter**. Requires application status in `{submitted, under_review}`. `decision="approved"` creates the `loans` row (status `approved`), a `loan_terms_snapshots` row, the `loan_schedule`, and a `final_terms` `member_consents` row (calls observer with the final-terms token); application → `awaiting_borrower_final_confirmation`. `decision="rejected"` → application `rejected`.

Consent (`app/domains/consent/service.py`) — public, slug+token driven, run as tenant
- `async def send_otp(slug, token, ip, sender: OtpSender) -> None` — generates a 6-digit code, stores `otp_hash`/`otp_expires_at` on the consent, then `await sender.send(phone_e164, code)` **after** the transaction commits.
- `async def decide(slug, token, code, decision: str, ip, user_agent) -> str` — `decision` in `{confirm, reject}`. Requires `otp_hash` set and valid (so `send_otp` must run first). On `confirm`: `applicant_ack` → application `under_review`; `final_terms` → loan+application `ready_for_disbursement`. On `reject`: opens a `disputes` row, application → `disputed`, and (if a loan exists) loan → `disputed`.

Disbursement (`app/domains/disbursements/service.py`)
- `async def prepare(current, loan_id, ip) -> uuid.UUID | None` — role **treasurer**. Requires loan `ready_for_disbursement`, a **verified** destination bank account whose details match the terms snapshot, no open dispute, and current eligibility. Creates `disbursements` (status `pending_authorization`).
- `async def authorize(current, disbursement_id, ip) -> bool` — role admin/committee; authorizer must differ from preparer, submitter, and all approvers. → `authorized`.
- `async def record_transfer(current, disbursement_id, reference: str, transfer_date: date, ip) -> bool` — role treasurer; recorder must differ from the authorizer. → `transfer_recorded`.
- `async def confirm_paid(current, disbursement_id, ip) -> uuid.UUID | None` — role admin/committee; caller **must be the authorizer** and differ from preparer/submitter/approvers. Posts the disbursement journal entry, sets loan+application → `repaying`, activates the schedule.

Repayment (`app/domains/repayments/service.py`)
- `async def post_repayment(current, *, loan_id, member_id, amount_kobo, source: str, received_on: date, reference: str, idempotency_key: str, ip) -> uuid.UUID` — roles officer/treasurer/admin; loan in `{repaying, disputed}`. Allocates by the policy `payroll_allocation_order` over due installments (`dues_kobo`/`savings_kobo` are 0 in the schedule, so it covers interest then principal). When outstanding principal hits 0 → loan `closed`, savings locks released.

Members (`app/domains/members/service.py` + `salary.py` + `bank_accounts.py`)
- `def encrypt_account_number(account_number: str) -> str` — Fernet (`_fernet()`).
- `async def record_contribution(current, member_id, kind: str, amount_kobo, idempotency_key) -> uuid.UUID` — roles officer/treasurer/admin; `kind` in `{savings, shares, dues}`; posts via `post_journal_entry`.
- `async def import_members(current, csv_text: str, confirm: bool) -> dict[str, Any]` — roles officer/admin. CSV header must include `member_no, full_name, phone_e164` (optional `email, staff_no, employer, joined_on`). Returns `{"imported", "errors", "valid_rows"}`. **(Enhanced — see §2.)**
- `async def add_salary_record(current, member_id, *, effective_from: date, basis: str, amount_kobo: int, source="declared") -> uuid.UUID` — roles officer/admin; audited (`salary_record_added`); tenant-isolated via `transaction()`. **Already exists.**
- `async def add_bank_account(current, member_id, *, bank_code, account_number, account_name, mark_verified=False) -> uuid.UUID` — roles officer/admin; stores `account_number_encrypted` (Fernet) + `last4`; `mark_verified=True` sets `status='verified'` + `verified_at`; audited (`bank_account_added`); tenant-isolated. **Already exists.**

Auth / staff (`app/domains/auth/service.py`, `app/domains/auth/staff.py`)
- `async def create_staff_user(current, *, email, password, roles: tuple[str, ...]) -> uuid.UUID` — role admin; audited (`staff_user_created`). **Already exists.**
- `async def _create_staff_user_in_session(session, *, cooperative_id, email, password, roles, actor_user_id, actor_role, channel) -> uuid.UUID` — the in-session variant (writes `users`/`user_roles` + audit). **Already exists** — the CLI refactor (§2) calls this.
- `async def resolve_tenant_slug(slug) -> tuple[uuid.UUID, str] | None` — reads `tenant_directory` (no RLS) before tenant context.

Core
- `app.core.db.transaction(cooperative_id) -> AsyncSession` context manager — sets `app.cooperative_id` via `SET LOCAL`, runs as `ajo_app`.
- `app.core.config.get_settings()` / `Settings.is_production`.
- `app.domains.audit.service.append_audit(...)` — used internally by every service above.
- `app.domains.loans.calculations.build_schedule(principal_kobo, annual_rate_bps, tenor_months, method, first_due_date) -> list[Installment]` — pure; the seed may call it to compute a loan's total payment for the close-out repayment.

---

## 2. Services to add / change (small, in-scope)

None of the task's named "to add" services are actually missing — `add_salary_record`, `add_bank_account`
(with `mark_verified`, storing `last4`, reusing `encrypt_account_number`), and `create_staff_user` all
already exist with the requested shapes. So **do not create them again**. Only two small additions are
needed so the seed can stay raw-SQL-free:

- [ ] 2a. Add `async def get_bootstrap_admin(cooperative_id: uuid.UUID) -> tuple[uuid.UUID, str] | None` to `backend/app/domains/auth/staff.py`.
      Reads the cooperative's single admin (`users` JOIN `user_roles` WHERE `role='admin'`, `LIMIT 1`) inside `transaction(cooperative_id)`, returns `(user_id, email)`. Rationale: the seed must build an admin `CurrentSession` to call `create_staff_user`, `approve_policy`, `approve_loan_product`, and the disbursement `authorize`/`confirm_paid`; no existing service exposes the bootstrap admin's id, and a raw lookup would violate "no raw SQL in seed_demo.py". It is a read (no audit needed).
      Files: `backend/app/domains/auth/staff.py`
      Verify: `cd backend && .venv/bin/mypy app` passes; `ruff check` clean.

- [ ] 2b. Enhance `import_members` to return the created member ids. Change the member `INSERT` to `... RETURNING id` and add `"members": [{"member_no": <no>, "id": <uuid>}, ...]` (in insertion order) to the returned dict. Backward-compatible (existing callers ignore the extra key).
      Rationale: the seed needs each member's id to drive salary/bank/contribution/loan services without a raw `SELECT`.
      Files: `backend/app/domains/members/service.py`
      Verify: `cd backend && .venv/bin/pytest tests/integration/test_members.py -q` (existing `test_csv_dry_run_writes_nothing` still passes; new return key present) — owner runs this; coder runs only ruff+mypy on the file.

- [ ] 2c. Refactor `create_cooperative` to call the existing reusable helper instead of inlining staff-user SQL. Replace the inline `INSERT INTO users` / `INSERT INTO user_roles` / `append_audit(... "cooperative_bootstrapped" ... entity_type="user")` block with a call to `_create_staff_user_in_session(session, cooperative_id=cooperative_id, email=email, password=temporary_password, roles=("admin",), actor_user_id=user_id, actor_role="admin", channel="cli")`. Keep the cooperative / `create_default_chart` / `tenant_directory` inserts. (No test covers `create_cooperative`, so this is low-risk; the audit action for the admin becomes `staff_user_created`.)
      Files: `backend/app/cli.py`
      Verify: `cd backend && .venv/bin/python -m app.cli --help` still shows `create-cooperative` (read-only sanity check permitted by the task); ruff + mypy clean.

---

## 3. Migrations

**None.** `salary_records`, `bank_accounts`, `users`, `user_roles`, `cooperative_policy_versions`,
`loan_products`, `members`, `loan_applications`, `loans`, `loan_schedule`, `member_consents`,
`notifications`, `disputes`, `disbursements`, `repayments`, `accounts`, `audit_logs` all exist (migrations
0001–0010). `notifications.status` already permits `'failed'`, which is the dead-letter value the
`notifications-dead` command lists. Do **not** run `make migrate` for this change.

---

## 4. seed-demo design

New module `backend/app/seed_demo.py` exposing `async def seed_demo(slug: str, yes_fake_data: bool) -> dict[str, Any]`
(returns a counts summary; prints staff temp passwords once and a summary — never account numbers). Wire a
`seed-demo` subcommand in `backend/app/cli.py` (`--slug` required, `--yes-fake-data` flag) that calls it and
exits non-zero on refusal/error. The seed runs **only** through services via `transaction()` (tenant role
`ajo_app`) — never the owner role, never raw SQL.

### Refusals (check in this order, raise `RuntimeError` / print + exit 1)
1. `get_settings().is_production` → refuse ("refusing to seed in production"). Checked first, before slug resolution, so the production test needs no cooperative.
2. `not yes_fake_data` → refuse ("pass --yes-fake-data to confirm fake demo data").
3. `resolve_tenant_slug(slug)` is `None` → refuse ("slug does not exist").
4. `count(*) FROM members` for that cooperative > 0 (read inside `transaction(cooperative_id)`) → refuse ("slug already has members").

### Determinism / secrets
- `random.seed(20240501)` for demo data (names, phones, account-number digits, principals, tenors).
- Staff temp passwords use `secrets.token_urlsafe(18)` (NOT the fixed seed) — printed once, never logged elsewhere.
- Consent tokens/OTP are generated by the services via `secrets` at runtime; the seed captures them (see below), so no determinism is needed there.

### Bootstrap admin session
- `admin_id, admin_email = await get_bootstrap_admin(cooperative_id)`; build `admin_session = CurrentSession(cooperative_id, slug, uuid.uuid4(), admin_id, admin_email, ("admin",), bytes(32))`. (`CurrentSession` is a frozen dataclass; services only read `cooperative_id`, `user_id`, `roles`, `slug` — `session_id`/`csrf_hash` are placeholders, matching the test pattern in `tests/integration/test_loans.py`.)

### Staff users (4 new + existing admin = 5)
Create via `create_staff_user(admin_session, email=<role>@<slug>.demo, password=<secrets>, roles=(<role>,))` for `officer`, `committee`, `treasurer`, `auditor`. Capture each returned `user_id` and print each one-time temp password. Build a `CurrentSession` per staff user (placeholder `session_id`/`csrf_hash`).

### Policy + product (different users propose and approve)
- `officer_session` calls `propose_policy(values, reason="demo policy")`; `admin_session` calls `approve_policy(policy_id, reason="demo approval")` (proposer ≠ approver).
- `values = {effective_from: date.today(), salary_basis: "net", deduction_cap_bps: 7000, max_active_loans: 1, guarantors_required: 0, min_savings_kobo: 0, loan_to_savings_multiple_x100: 10000, interest_method: "reducing_balance", payroll_allocation_order: ["dues","interest","principal","savings"], confirmation_expiry_minutes: 1440, otp_validity_seconds: 600, settings: {}}`.
- `officer_session` calls `create_loan_product(product, date.today(), reason="demo product")`; `admin_session` calls `approve_loan_product(product_id, date.today(), reason="demo approval")`.
- `product = {code: "demo-staff", name: "Demo staff loan", interest_method: "reducing_balance", annual_rate_bps: 2400, min_amount_kobo: 5_000_000, max_amount_kobo: 50_000_000, min_tenor_months: 3, max_tenor_months: 24, fee_bps: 0, guarantors_required: 0, supersedes_id: None}`.

### 25 members (+ phones, bank accounts, salaries, contributions)
- Build one CSV string (header `member_no,full_name,phone_e164,email,staff_no,employer,joined_on`) with 25 fictional rows: `member_no` `M-001`..`M-025`; phones `+2348005550101`..`+2348005550125` (valid E.164, no spaces); deterministic names.
- `imported = await import_members(officer_session, csv_text, confirm=True)`; map `member_no -> id` from `imported["members"]`.
- For each member (officer_session): `add_salary_record(member_id, effective_from=date.today(), basis="net", amount_kobo=50_000_000, source="declared")`.
- For each member (officer_session): `add_bank_account(member_id, bank_code="001", account_number=<10 unique digits>, account_name=<name>, mark_verified=<True for the 14 borrowing members, False for the other 11>)`. Distinct account numbers per member (avoids the "shared destination account" fraud rule). Never print the account number (only `last4` exists in storage anyway).
- For each member (officer_session, three calls): `record_contribution(member_id, "savings", 5_000_000, idempotency_key=f"seed-sav-{member_no}")`, then `"shares"` 1_000_000, then `"dues"` 500_000.
- Eligibility is satisfied: salary ₦500,000 × 70% = ₦350,000 capacity >> any instalment; savings ₦50,000 × 100x = ₦5,000,000 limit >> any principal; `min_savings_kobo=0`; `guarantors_required=0`; one loan per borrowing member (no active-loan conflict).

### Capturing consent tokens + OTP (the real confirmation path)
- `class _CapturingOtp:` with `async def send(self, destination, code): self.codes[destination] = code` (one shared instance).
- Per loan, `tokens: list[str] = []` and an observer `lambda t: tokens.append(t)` passed as `consent_delivery_observer` to `submit_application` and `decide_application`. `tokens[0]` = applicant_ack token (created during submit); `tokens[1]` = final_terms token (created during `decide_application("approved")`), when applicable.
- To confirm/reject a consent: `await send_otp(slug, token, "127.0.0.1", sender); code = sender.codes[member_phone]; await decide(slug, token, code, <decision>, "127.0.0.1", "seed-demo")`.

### The 14 loans — exact per-state service call sequences
Use members `M-001`..`M-014` (one loan each; members 15–25 have no loans). Principals 10,000,000–30,000,000 kobo, tenors 6–12 months (instalment always < ₦350,000 capacity). For every disbursing loan, the member's bank account is `mark_verified=True`.

- **3 × awaiting_applicant_confirmation (application-only, no loan row):**
  `submit_application(officer_session, member_id, product_id, principal_kobo, tenor_months, destination_bank_account_id=<member's bank id>, guarantors=None, consent_delivery_observer=observer)` → STOP (leave the `applicant_ack` consent pending).

- **2 × under_review (application-only, no loan row):**
  submit as above → `send_otp` + `decide(slug, tokens[0], code, "confirm", …)` (application → `under_review`) → STOP.

- **2 × approved awaiting final confirmation (loan row, status `approved`; application `awaiting_borrower_final_confirmation`):**
  submit → confirm applicant_ack (→ `under_review`) → `decide_application(committee_session, app_id, "approved", "demo", consent_delivery_observer=observer)` (creates loan + `final_terms` consent) → STOP (leave `final_terms` pending).

- **1 × ready_for_disbursement (loan row, `ready_for_disbursement`):**
  as approved-awaiting-final, then `send_otp` + `decide(slug, tokens[1], code, "confirm", …)` (loan+application → `ready_for_disbursement`) → STOP (do not call `prepare`).

- **4 × repaying (loan row, `repaying`; 2–3 repayments each):**
  as ready-for-disbursement, then: `d = await prepare(treasurer_session, loan_id, "127.0.0.1")` → `await authorize(admin_session, d, "127.0.0.1")` → `await record_transfer(treasurer_session, d, f"BANK-REF-{i}", date.today(), "127.0.0.1")` → `await confirm_paid(admin_session, d, "127.0.0.1")` (loan → `repaying`). Then 2–3 `post_repayment(officer_session, loan_id=loan_id, member_id=member_id, amount_kobo=<partial>, source="cash", received_on=date.today()+timedelta(days=35+30*k), reference=f"PAY-{i}-{k}", idempotency_key=f"seed-pay-{i}-{k}", ip=None)` — partial amounts so the loan stays `repaying`.

- **1 × closed (loan row, `closed`):**
  full disbursement chain (as above), then one `post_repayment` for the loan's **total** schedule payment with `received_on=date(2099,1,1)` (all instalments become due): compute `total = sum(inst.payment_kobo for inst in build_schedule(principal_kobo, annual_rate_bps, tenor_months, "reducing_balance", datetime.combine(date.today()+timedelta(days=32), time.min, WAT)))`, then `post_repayment(officer_session, loan_id, member_id, amount_kobo=total, source="bank_transfer", received_on=date(2099,1,1), reference="CLOSE", idempotency_key=f"seed-close-{i}", ip=None)` → outstanding principal 0 → loan `closed`.

- **1 × disputed — STOP at the last reachable state (application-level disputed, no loan row):**
  Intended path (disputed **loan**) — `submit → confirm applicant_ack → decide_application("approved") → send_otp(final_terms) → decide(…, "reject", …)` — is **NOT reachable**: `consent.decide` reject inserts into `disputes` with **both** `application_id` and `loan_id` non-null, which violates the `disputes` CHECK `((application_id IS NOT NULL)::int + (loan_id IS NOT NULL)::int = 1)` (exactly one must be non-null), so the insert raises and the transaction rolls back.
  Last reachable disputed state: dispute at the `applicant_ack` stage (loan_id is NULL → `1+0=1` satisfies the CHECK): `submit → send_otp(tokens[0]) → decide(slug, tokens[0], code, "reject", …)` → application `disputed` (no loan row, `disputes` row with `application_id` only, `consent_dispute` fraud alert). Produce this and STOP. **Report the blocker** (see §9) and do not bypass the CHECK (it is a binding data-model rule, §9; changing it needs the owner's approval).

### Resulting counts (for the test)
- `loan_applications` = 14. `loans` = 8 (2 approved + 1 ready_for_disbursement + 4 repaying + 1 closed). The disputed case is an application only.
- Application-status counts: `awaiting_applicant_confirmation`=3, `under_review`=2, `awaiting_borrower_final_confirmation`=2, `ready_for_disbursement`=1, `repaying`=5 (4 repaying loans + the closed loan's application, which stays `repaying` because `post_repayment` does not update `loan_applications` on close), `disputed`=1.
- Loan-status counts: `approved`=2, `ready_for_disbursement`=1, `repaying`=4, `closed`=1.
- `members` = 25. Staff users (distinct users with a role) = 5 (admin + officer + committee + treasurer + auditor).

### What the seed prints
- The 4 new staff temp passwords (once).
- A summary line: members=25, loan_applications=14, loans=8, staff_users=5, plus the disputed-state note. **Never** print bank account numbers (only `last4` is ever stored/usable).

---

## 5. notifications-dead command

Add a `notifications-dead` subcommand in `backend/app/cli.py` (`--slug` required). Resolve the slug via
`resolve_tenant_slug`; refuse if missing. Then read inside `transaction(cooperative_id)`:

```
SELECT id, member_id, kind, attempts, created_at, next_attempt_at, sent_at
FROM notifications
WHERE cooperative_id = :cid AND status = 'failed'
ORDER BY created_at
```

Print rows as a plain table. **Do not select `payload_encrypted`** (the task: "without payloads"). Dead-letter
= `status='failed'` (the only failed-state value in the `notifications` CHECK; the worker skeleton describes
dead-lettering-after-N-failures but does not implement it, so a freshly seeded cooperative lists nothing —
correct). No new table/column/migration. Runs as `ajo_app` with tenant context (compliant).

---

## 6. The 3 tests (only these; matching `tests/integration` conventions)

Conventions (from `tests/conftest.py` + `tests/integration/test_*.py`): tests use the local Postgres
`test_database` fixture (`ajo_app` role, `ajo_test` DB, alembic-upgraded per session), connect via
`asyncpg.connect(asyncpg_dsn(test_database.app_url))`, set RLS context with
`SELECT set_config('app.cooperative_id', $1, true)` inside a transaction, and build `CurrentSession`
directly (`CurrentSession(cid, slug, uuid.uuid4(), uid, email, roles, bytes(32))` — placeholder
`session_id`/`csrf_hash`). Every async test carries `pytestmark = pytest.mark.db` and uses an autouse
`reset_engine`/`dispose_engine` fixture.

- [ ] 6a. `tests/integration/test_seed_demo.py::test_seed_demo_counts_and_balanced_journals`
      - `await dispose_engine()`; `slug = "seedtest"`; `admin_pw = await create_cooperative(slug, "Seed test", "admin@seed.test")` (uses `DATABASE_URL_DIRECT` = `TEST_OWNER_URL` in tests; creates cooperative + `tenant_directory` + admin + chart).
      - `await seed_demo(slug, yes_fake_data=True)`.
      - Connect as `ajo_app`; assert via asyncpg (with tenant context):
        `count(members)=25`; `count(loan_applications)=14`; `count(loans)=8`;
        app-status counts (3/2/2/1/5/1) and loan-status counts (2/1/4/1) as in §4;
        `count(distinct users having a user_roles row for this cooperative)=5`;
        `SELECT count(*) FROM (SELECT entry_id, sum(debit_kobo) d, sum(credit_kobo) c FROM journal_lines WHERE cooperative_id=:cid GROUP BY entry_id) t WHERE d<>c` = 0; and `SELECT sum(debit_kobo)=sum(credit_kobo)` overall.
      Files: `backend/tests/integration/test_seed_demo.py` (new)
      Verify (owner): `cd backend && .venv/bin/pytest tests/integration/test_seed_demo.py::test_seed_demo_counts_and_balanced_journals -q`.

- [ ] 6b. `tests/integration/test_seed_demo.py::test_seed_demo_refuses_in_production`
      - `monkeypatch.setenv("APP_ENV", "production")`; `get_settings.cache_clear()` (conftest already sets valid `SECRET_KEY`/`ENCRYPTION_KEY`, so the production validator passes).
      - `with pytest.raises(RuntimeError, match="production"): await seed_demo("any-slug", yes_fake_data=True)` (production check is first, so no cooperative is needed).
      - Teardown: `get_settings.cache_clear()`; monkeypatch restores `APP_ENV`.
      Files: `backend/tests/integration/test_seed_demo.py` (same new file)
      Verify (owner): `cd backend && .venv/bin/pytest tests/integration/test_seed_demo.py::test_seed_demo_refuses_in_production -q`.

- [ ] 6c. `tests/integration/test_members.py::test_salary_and_bank_account_tenant_isolated_and_audited`
      - Reuse that file's `setup_tenant`/`session` helpers. Create tenant A (with a member) and tenant B via `create_tenant`.
      - In tenant A: `sid = await add_salary_record(session(cidA, uidA, "officer"), memberA, effective_from=date.today(), basis="net", amount_kobo=1_000_000, source="declared")`; `bid = await add_bank_account(session(cidA, uidA, "officer"), memberA, bank_code="001", account_number="1234567890", account_name="A", mark_verified=False)`.
      - Audited: in tenant A context, `SELECT count(*) FROM audit_logs WHERE action='salary_record_added' AND entity_id=:sid` = 1, and `… action='bank_account_added' AND entity_id=:bid` = 1.
      - Tenant-isolated: set context to tenant B; `SELECT count(*) FROM salary_records WHERE id=:sid` = 0 and `SELECT count(*) FROM bank_accounts WHERE id=:bid` = 0 (RLS hides tenant A's rows).
      - (Extra safety) in tenant A: `SELECT last4 FROM bank_accounts WHERE id=:bid` = `'7890'` and `account_number_encrypted <> '1234567890'` (number not stored in plaintext).
      Files: `backend/tests/integration/test_members.py` (append one test)
      Verify (owner): `cd backend && .venv/bin/pytest tests/integration/test_members.py::test_salary_and_bank_account_tenant_isolated_and_audited -q`.

---

## 7. Verification (what the coder runs — ruff + mypy only; `--help` is read-only)

```
cd backend && .venv/bin/ruff check app tests
cd backend && .venv/bin/ruff format --check app tests
cd backend && .venv/bin/mypy app
cd backend && .venv/bin/python -m app.cli --help          # read-only; confirm seed-demo + notifications-dead register
```
Expected: ruff clean, `ruff format --check` clean, `mypy app` clean, and `--help` lists `create-cooperative`,
`verify-audit`, `seed-demo`, `notifications-dead`.

---

## 8. Owner commands (run after the coder reports ruff+mypy clean)

```
# the 3 new/changed tests
cd backend && .venv/bin/pytest tests/integration/test_seed_demo.py tests/integration/test_members.py -q

# full suite once at the end
make test

# exercise the real CLI on the dev branch (NOT Neon; never production)
cd backend && .venv/bin/python -m app.cli create-cooperative --slug demo --name "Demo coop" --admin-email admin@demo.test
cd backend && .venv/bin/python -m app.cli seed-demo --slug demo --yes-fake-data
cd backend && .venv/bin/python -m app.cli notifications-dead --slug demo
```
**No migration was written for this change, so `make migrate` is NOT required.**

---

## 9. Decision the owner must make (disputed-loan state is unreachable)

A loan in `disputed` status (a `loans` row with `status='disputed'`) **cannot be produced through the existing
consent service**. The only dispute-opening path is `consent.decide(…, "reject", …)`, and for a final-terms
reject (where a loan exists) it inserts `disputes` with both `application_id` and `loan_id` set, violating the
`disputes` CHECK `((application_id IS NOT NULL)::int + (loan_id IS NOT NULL)::int = 1)`. This is a code/schema
conflict (consent assumes both can be set; migration 0007 forbids it) and is untested.

Per the rules I did **not** bypass it: the seed produces the last reachable disputed state — a disputed
**application** (applicant_ack reject, `loan_id` NULL, CHECK satisfied) — and stops.

To instead produce a disputed **loan**, one of these (each touches binding data-model rule §9 / a migration and
needs your explicit approval) would be required:
- Relax the `disputes` CHECK to `((application_id IS NOT NULL)::int + (loan_id IS NOT NULL)::int <= 1)` (a
  dispute may legitimately reference both its application and its loan), via a new Alembic migration; or
- Change `consent.decide` reject to insert `loan_id` only (NULL `application_id`) when a loan exists.

Tell me which (if either) you want, and I'll plan that as a separate approved change.
