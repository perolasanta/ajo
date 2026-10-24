# Ajo — Project Context (v2)

**Product:** Ajo, a cooperative management and loan administration SaaS for Nigerian cooperative societies.
**Version:** 2.0, 23 October 2026. Supersedes the earlier `context.md` and incorporates `ajo_blueprint_review_and_bot_channel_plan.md` and an independent second review.
**Owner:** solo founder-developer. **Assistant role in this project:** senior engineer and product partner who follows this file, challenges it when it is wrong, and updates it when decisions change.

---

## 0. How the assistant must use this file

1. Treat Sections 2, 9, 10 and 12 as binding. If a request conflicts with them, say so and propose an alternative before writing code.
2. Anything marked **CONFIRM** is an assumption awaiting the EXCO or a lawyer. Build it configurable, never hard-coded.
3. Do not invent business rules. If a rule is missing, add it to Section 21 (Open questions) and use the default in Section 20.
4. Never put financial logic in a Telegram/WhatsApp handler or a React component. All rules live in the FastAPI service layer.
5. Small, reviewable steps: one migration or one feature slice per change, with tests. After any decision change, update this file in the same change.
6. This file is not legal advice. Legal and regulatory items need a Nigerian lawyer's written opinion.

---

## 1. Product definition

Ajo is a **financial recordkeeping system that prevents unauthorized loans and salary deductions**, keeps trustworthy accounts, and gives members visibility into their money. It is not "a loan app with a database".

**The core promise to a cooperative:** *No loan is disbursed and no salary deduction is scheduled unless the person who will repay it has personally confirmed the final terms. Every balance is explainable, every change is traceable, and the history cannot be silently altered.*

### Product principles

1. **Borrower consent is mandatory for every loan**, including when the borrower is the applicant. Staff can assist but never submit as a member.
2. **Server-side enforcement.** UI and bots are thin clients.
3. **Append-only money.** Corrections are reversals, never edits.
4. **Explainability.** Every figure shown (capacity, balance, deduction) can show how it was computed.
5. **Ajo never holds or moves member money.** The cooperative's own bank account does. Ajo records and recommends; humans decide.
6. **Members meet Ajo where they already are** (WhatsApp/Telegram, plus a tiny mobile web page). Officers get a fast, rich portal.
7. **Honest operations.** Break-glass access, audit trail visible to the cooperative, tested backups.

---

## 2. Decisions log (locked unless this section is edited)

| # | Decision | Reason |
|---|---|---|
| D1 | Backend: **FastAPI** (async), Python 3.12+, Pydantic v2 | Owner's stack |
| D2 | Telegram: **aiogram 3**, webhook mode | Owner's stack (not python-telegram-bot) |
| D3 | WhatsApp: **official WhatsApp Business Platform (Cloud API)** only. No unofficial libraries | Ban risk, reliability |
| D4 | Database: **PostgreSQL on Neon**, region **aws-eu-central-1 (Frankfurt)** | Owner chose Neon to learn it. Same region as the Frankfurt VPS |
| D5 | Compute for the Neon production branch is **always-on** (scale-to-zero disabled) | Webhooks must not hit cold starts |
| D6 | Accounting: **double-entry from the first migration** (balanced journal entries and lines) | Most expensive thing to retrofit |
| D7 | One authoritative financial ledger. Applications, approvals, consents, disputes, payroll imports, notifications are **separate operational tables**, linked to journal entries when they have financial effect | Keeps the ledger clean |
| D8 | Confirmation security: **no PIN typed into chat.** Single-use signed link to a confirmation page plus OTP on a second channel for high-risk actions | Chat history is not a safe place for secrets |
| D9 | Background work: **Postgres outbox** + separate worker process (`FOR UPDATE SKIP LOCKED`). No Redis/Celery in MVP | Fewer moving parts for a solo build |
| D10 | Tenant isolation: tenant checks in the service layer **plus** Postgres RLS using `SET LOCAL` per transaction | Defence in depth |
| D11 | Roles are a fixed enum in MVP: `member, officer, committee, treasurer, admin, auditor` (+ platform `operator`) | Simplicity; configurable roles later |
| D12 | Bank disbursement is **manual with independent authorization** in MVP. No payment-provider automation yet | Highest-risk step; automate only after trust |
| D13 | Frontend: **React + TypeScript + Vite**, Tailwind, shadcn/ui, TanStack Query and Table, served as static files behind the existing Nginx on the VPS | Fast back-office SPA, typed API client |
| D14 | Reverse proxy: the **existing Nginx on the VPS**. Ajo containers bind to `127.0.0.1` only; Nginx gets a separate virtual host. Validate with `nginx -t` before reload. Never replace the existing Nginx config | Preserve the VPS's existing reverse-proxy setup |
| D15 | Member web surface is a tiny **mobile-first page** (confirmation, statement, capacity). Everything else for members is in chat | Low bandwidth, low-end phones |
| D16 | Telegram first for the pilot; **WhatsApp business verification starts in Phase 1** in parallel | Verification and template approval have lead time; members mostly use WhatsApp |
| D17 | Neon services other than Postgres (Auth, Object Storage, Functions, AI Gateway, Realtime) stay **OFF**. Ajo builds its own auth. Document storage will be decided later; when chosen, it will be S3-compatible in Frankfurt | Keep the MVP portable and limit enabled services |
| D18 | Codex works only against the Neon **dev branch**. No Neon MCP server and no production credentials | Protect production data and credentials |

### Reconciliation with the two reviews

- Adopted from both: mandatory borrower consent, double-entry, Postgres outbox, RLS plus roles, never hold member money, lead sales with "no unauthorized deductions", per-active-member pricing with pass-through messaging, data location decided with legal advice.
- **Changed after review:** the PIN-in-chat template is replaced by D8. Confirmation window is 24 hours (configurable); OTP stays short-lived. A rejection opens a dispute flow and does not irreversibly freeze the member's account.
- **Database:** the second review suggested Supabase for MVP. Owner chose Neon deliberately; Neon is plain Postgres, so the code stays portable (no vendor-specific features, Section 7).
- **WhatsApp cost:** sources disagree on Nigeria's utility rate (about $0.0067 to $0.0101 per delivered message). Use that range for planning, confirm on Meta's current rate card, and build a usage report. At 100,000 messages a month that is roughly $670 to $1,010. Also verify whether in-window service replies became chargeable from 1 October 2026 as one source reports.
- **Regulation:** a law-firm summary reports that the FCCPC DEON guidelines exclude duly registered and licensed cooperative societies. Treat as promising, not settled. Ajo's own role as software provider still needs a written opinion (Section 12).
- A signed button tap is **not proof of identity**. Authentication strength must match risk (Section 11).

---

## 3. Competitive position

Public descriptions of Nigerian competitors (CLÉ COOP, CWG Unified Cooperative Platform, Wired-Banking and various custom or Excel-based tools) emphasise member management, contributions, loan processing, reports, notifications and sometimes Excel posting of monthly deductions. Nothing I found highlights borrower-first consent, tamper-evident ledgers, dispute-to-freeze, or chat-first member access. Verify claims during customer discovery rather than relying on this.

### How Ajo wins (measurable)

| Area | Typical offering | Ajo target |
|---|---|---|
| Unauthorized loans | Officer enters, member signs paper | Borrower confirms terms hash on their own channel before disbursement. One-tap dispute freezes deductions |
| Trust in numbers | Editable records | Balanced, append-only ledger, hash-chained audit log, signed opening balances |
| Deduction capacity | Simple eligibility check | Explainable capacity with "partial check" warning when external deductions are unknown |
| Payroll deductions | Manual Excel posting | Import payroll result, auto-match, exceptions queue, partial-payment allocation |
| Member access | Web portal or app install | Telegram/WhatsApp commands, no install, plus a light mobile page |
| Onboarding | Weeks of consulting | Migration tool with reconciliation report and EXCO sign-off |
| Officer UX | Dense legacy forms | Fast keyboard-friendly portal with command palette, saved filters, loan timeline |
| Audit | Reports on request | Auditor role, immutable evidence packs, export on demand |
| Backend quality | Unknown | p95 server time under 300 ms for common reads, zero double-posting under concurrency tests, restore drills passed |

**Positioning line:** "The cooperative system where no one can borrow in your name."

---

## 4. Users, roles, channels

| Role | Primary surface | Main powers |
|---|---|---|
| member | Telegram/WhatsApp + member page | View own data, apply, confirm, dispute |
| officer | Portal | Capture applications on behalf of members (member must still confirm), maintain records |
| committee | Portal | Review and approve loans |
| treasurer | Portal | Record disbursement reference, run dues/payroll files |
| admin | Portal (MFA) | Cooperative settings, user management, binding codes |
| auditor | Portal (read-only) | Ledger, audit log, evidence packs |
| platform operator (founder) | Admin tooling | Break-glass only, with recorded reason visible to the cooperative |

Separation rules: the person who approves cannot authorize disbursement; the person who creates an application cannot approve it; one person cannot hold conflicting roles on the same loan.

---

## 5. Scope

### MVP (must ship for first paying pilot)
Tenant and member model, salary records, double-entry ledger, dues and savings, loan products, application and eligibility engine, committee approval, borrower confirmation (two points), manual disbursement with authorizer, repayment schedule, audit log, fraud alerts (5 rules), officer portal, Telegram channel (binding, balance, loan, schedule, pending, dispute), notification outbox, payroll export/import and reconciliation, opening-balance migration with sign-off.

### Later
WhatsApp production adapter, PDF statements, bank-account change workflow with cooling-off, payment provider integration, year-end processes (interest on savings, dividends, AGM reports), per-tenant white-label bots, USSD for feature phones, configurable roles, multi-approver thresholds, Pidgin/Yoruba/Hausa/Igbo message variants.

### Explicitly out of scope
Holding member funds, wallets, card issuing, automated credit scoring, any ML-based approvals.

---

## 6. Architecture and infrastructure

```text
Telegram webhook ─┐                                   ┌─> Postgres (Neon, Frankfurt)
WhatsApp webhook ─┼─> existing Nginx ─> FastAPI (api) ┤     state, ledger, audit, outbox
Web portal (SPA) ─┘              │  service layer     └─> RLS + roles
Member page (signed link) ───────┘
                          Worker process (outbox, retries)
                          Scheduler process (dues, schedules, reminders)  ← single instance
```

- **VPS (Frankfurt):** Docker Compose with services `api`, `worker`, `scheduler`; Ajo containers bind to `127.0.0.1` only. The existing host Nginx routes a separate virtual host to Ajo. Validate changes with `nginx -t` before reload and never replace the existing Nginx config. The API can run several workers; scheduler and worker run as separate containers.
- **Neon:** project in `aws-eu-central-1`, co-located with the VPS so the app never pays a Lagos-to-Europe round trip on database calls.
- **Environments:** Neon branches `main` (production), `staging`, `dev`. Never copy production data to a dev branch without masking personal data.
- **Webhook rules:** verify Telegram's secret-token header (`X-Telegram-Bot-Api-Secret-Token`) and WhatsApp's `X-Hub-Signature-256`. Persist the event, acknowledge fast, process in the worker. Deduplicate on Telegram `update_id` and WhatsApp message ID with a unique constraint.
- **Secrets:** environment variables from a secrets file outside git; rotate on staff change. Never log tokens, OTPs or full account numbers.
- **Cross-border:** data stored in Frankfurt is outside Nigeria. See Section 12 before the first contract.
- Neon Auth, Object Storage, Functions, AI Gateway and Realtime remain off. Ajo builds its own authentication. Document storage is a later decision; the intended location is Frankfurt and the storage must be S3-compatible.
- Codex may access only the Neon dev branch. Do not use the Neon MCP server or production credentials.

---

## 7. Neon operating rules

Neon facts used here (checked October 2026; reconfirm on neon.com/pricing): Launch plan is usage-based (compute per CU-hour plus storage per GB-month), plans include PgBouncer pooling and branching, compute autosuspends after 5 minutes by default and paid plans can disable that.

1. Production branch: autosuspend **off**, minimum compute 0.25 CU to start, autoscale up when load justifies. An always-on 0.25 CU costs roughly $19 a month at the Launch rate. Review the bill monthly.
2. **Two connection strings:**
   - *Pooled* (`-pooler` host) for the API and worker. PgBouncer is in transaction mode, so: use `pg_advisory_xact_lock` (transaction-level) only; set `SET LOCAL` for tenant context inside each transaction; configure asyncpg with `statement_cache_size=0` (and equivalent SQLAlchemy settings).
   - *Direct* for Alembic migrations and the scheduler (session-level features allowed).
3. Roles: `ajo_owner` (migrations only), `ajo_app` (API/worker), `ajo_readonly` (reporting, auditor exports). `ajo_app` has no UPDATE/DELETE on ledger and audit tables.
4. Use branches to rehearse every migration and every opening-balance import before running on `main`.
5. Check the **restore window and instant-restore behaviour** for the chosen plan, and test a restore. In addition run a nightly encrypted `pg_dump` to storage outside Neon and the VPS (for example a different provider's object storage). Keep a monthly restore drill record.
6. Stay portable: no Neon-specific SQL, no Neon Data API, no dependency on Neon Auth. Ajo handles its own authentication.
7. Keep a small "Neon notes" log in `/docs/neon.md` of what was learned (the owner is learning Neon as part of this build).

---

## 8. Backend standards

### Stack
Python 3.12, FastAPI, SQLAlchemy 2.0 async with asyncpg, Alembic, Pydantic v2, `structlog`, Sentry, `pytest` + `pytest-asyncio` + `hypothesis` (property tests for the ledger), `ruff`, `mypy --strict` on core domains, `argon2-cffi`, `pyotp`.

### Repository layout (monorepo)
```text
/backend
  /app
    /core        config, db, security, logging, errors, idempotency, time, money
    /domains
      /tenancy  /members  /ledger  /loans  /consent  /payroll
      /channels (telegram, whatsapp adapters + router)  /notifications
      /audit    /fraud    /documents  /reports
    /api         routers only: parse, authorize, call service, serialize
    /worker      outbox consumer
    /scheduler   periodic jobs with advisory-lock + idempotency keys
  /migrations    Alembic
  /tests         unit, integration, concurrency, property, security
/frontend        React app (Section 13)
/ops             docker-compose, Nginx virtual-host config, backup scripts, runbooks
/docs            context.md, neon.md, runbooks, ADRs
```

### Conventions
- Each domain exposes a **service** (`service.py`); routers and bots call services only.
- Money is `BIGINT` kobo in the database and an integer type in code. No floats. Interest uses `Decimal` with an explicit, documented rounding rule and a final-instalment adjustment.
- Storage time is UTC; payroll periods are computed in `Africa/Lagos` (WAT). Test month-end and boundary cases.
- Every state-changing endpoint accepts or derives an **idempotency key**; the business action, journal posting and idempotency record commit in one transaction.
- Errors are typed and never leak other members' data. API is versioned (`/v1`) and OpenAPI is the source for the frontend's generated client.
- Soft limits: functions short, no business logic in routers, no `SELECT *`, explicit transactions.

---

## 9. Data model

MVP tables (about 25; keep names stable):

`cooperatives, cooperative_policy_versions, policy_change_log, loan_products, cooperative_modules, users, user_roles, members, member_contacts, salary_records, bank_accounts, accounts (chart of accounts), journal_entries, journal_lines, loan_applications, loan_terms_snapshots, loans, loan_schedule, guarantees, savings_locks, approvals, disbursements, member_consents, disputes, payroll_runs, payroll_items, channel_bindings, inbound_events, notifications (outbox), audit_logs, fraud_alerts, idempotency_keys, documents, access_grants (break-glass)`

### 9.1 Policy and product configuration
- `cooperative_policy_versions` stores typed, validated columns for salary basis, deduction cap, guarantor rules and savings rules, plus an effective date, proposer and a different approver. JSONB is reserved for non-critical preferences.
- `policy_change_log` records policy changes and their approval history.
- `loan_products` may set product terms but cannot override cooperative-wide safeguards.
- Every eligibility decision and loan approval stores the `policy_version_id` used. Existing loans keep their contractual terms when a policy changes.

### 9.2 Operating models and modules
- Each tenant has an operating model: `loan_cooperative`, `thrift_society` or `rotating_ajo`.
- `cooperative_modules` tracks enabled modules per cooperative. Disabling a module never deletes financial history.
- MVP implements `loan_cooperative`; `thrift_society` comes next; implement `rotating_ajo` only after validation with a real group.
- Ledger account types distinguish withdrawable savings, compulsory shares, dues, locked savings and the rotating-Ajo pool. Money held for members is kept separate from the society's income.

### 9.3 Ledger
- `journal_entries`: id, cooperative_id, posted_at, effective_date, source_type, source_id, description, created_by, reverses_entry_id (nullable), idempotency_key.
- `journal_lines`: entry_id, account_id, member_id (nullable sub-ledger), loan_id (nullable), `debit_kobo`, `credit_kobo`. CHECK: exactly one of debit or credit is positive.
- **Balanced by the database:** a deferred constraint trigger verifies total debits equal total credits per entry at commit. Posting goes through one database function (`post_journal_entry`). The application role cannot UPDATE or DELETE; a trigger also raises on any such attempt.
- Chart of accounts (default, per cooperative): bank accounts, loans receivable, interest receivable, withdrawable savings, compulsory shares, dues, locked savings, rotating-Ajo pool, dues and fee income, interest income, reserves. Member-held money is separate from society income.
- Example: loan disbursement debits "Member loan receivable" and credits "Cooperative bank account" for the same amount. Repayment reverses direction.
- A balanced entry is not necessarily a correct one. The service validates loan, member, authorizations, amount and source before posting.
- Balances are derived from lines (with indexes and period snapshots later), never stored as editable numbers.

### 9.4 Audit
- `audit_logs`: append-only, with `prev_hash` and `hash` per row (hash chain) for tamper evidence. Record actor, role, action, entity, before/after summary (no secrets), channel, IP/device hint, reason where required.
- Break-glass access creates an `access_grants` row with a mandatory reason, visible to the cooperative's administrator and auditor.

### 9.5 Loan lifecycle
```text
draft → submitted → awaiting_applicant_confirmation → under_review → approved
      → awaiting_borrower_final_confirmation → ready_for_disbursement → disbursed → repaying → closed
side states: rejected, withdrawn, expired, disbursement_failed, restructured, written_off, disputed
```
- `disputed` freezes new deductions and raises a fraud alert; resolution is a controlled officer/admin process with evidence.
- `loan_terms_snapshots` stores canonical JSON of approved terms plus a SHA-256 hash. Any change to amount, tenor, rate, beneficiary or destination account invalidates prior confirmations.
- Disbursement is blocked unless `borrower_confirmed_at` is set **and** the confirmed hash equals the current approved snapshot hash.
- Every eligibility decision and loan approval records the `policy_version_id` applied; a policy change does not alter existing loan contractual terms.

---

## 10. Business rules

### 10.1 Eligibility and deduction capacity
```text
salary_basis         = net or gross salary (CONFIRM) for the payroll period
max_total_deductions = salary_basis × cap_pct                    (default 70%, CONFIRM)
committed            = existing loan instalments + savings contributions + monthly dues
                       + external_deductions                     (payroll-supplied or declared; may be unknown)
capacity             = max_total_deductions − committed
Approve only if: proposed_instalment ≤ capacity
```
Worked example: basis ₦250,000, cap 70% gives ₦175,000. Committed ₦60,000 + ₦20,000 + ₦1,000 = ₦81,000. Capacity is ₦94,000.
- If `external_deductions` is unknown, show a **"partial check"** warning to the committee instead of a clean pass.
- Exposure includes loans the member **guarantees**.
- Savings backing an active loan are **locked** (`savings_locks`); withdrawals are limited to the unlocked amount.
- Eligibility is recomputed at **submission, approval and just before disbursement**, inside the transaction.

### 10.2 Concurrency
- One-active-loan rule (default, CONFIRM) via a partial unique index on member for statuses in submitted/approved/disbursed/repaying.
- At approval and disbursement take `pg_advisory_xact_lock(hashtext(member_id::text))` (or `SELECT … FOR UPDATE` on the member row) and recompute exposure in the same transaction.
- Replays of webhooks, taps and API calls are harmless through idempotency keys and unique constraints.

### 10.3 Third-party loans (beneficiary ≠ borrower)
Allowed only if the EXCO approves the product (CONFIRM). The borrower always confirms; the beneficiary confirms additionally. Fraud rule flags beneficiary ≠ borrower for review.

### 10.4 Exits and changes
Resignation, retirement, death, dismissal, and mid-loan salary changes need defined recovery rules (savings, terminal benefits, guarantors, interest stop). Salary reduction that pushes the instalment over the cap raises a review flag.

### 10.5 Fraud rules (deterministic only, MVP)
1. Parallel or overlapping loans for one member.
2. Beneficiary ≠ borrower.
3. Destination account changed after approval.
4. Same destination account shared across members.
5. Same user initiated and authorized.
No scoring, no ML.

### 10.6 Year-end (later)
Interest on savings, dividends or patronage refunds, annual statements, AGM reports.

---

## 11. Member channel design

### 11.1 Identity binding
1. Admin/officer generates a **single-use, short-lived binding code** for the member (or the member requests one after identity checks verifiable from HR data).
2. Member opens the bot (Telegram deep link or WhatsApp message). The system verifies the code; on WhatsApp it also checks the sender's number against the number on record.
3. Binding stored in `channel_bindings (member_id, channel, external_id, bound_at, status)`.
4. The member is notified on any other known channel that a binding was created.
5. **Re-binding** (new phone, lost SIM, SIM swap) needs officer verification, a cooling-off period, notice to the old channel, and an audit event.

### 11.2 Risk levels
| Level | Actions | Authentication |
|---|---|---|
| Low | Help, menu, contact info | Bound channel |
| Medium | Balance, loan status, schedule, statement | Bound channel + short session (OTP or signed link) |
| High | Confirm application, accept final terms, confirm as beneficiary, dispute | Signed single-use link **plus OTP to a second channel** (or equivalent second factor) |
| Never in chat | Bank-account change, salary change, approvals, disbursement, role changes | Web portal with MFA and maker-checker |

A member who cannot use Telegram must have a documented alternative that still verifies identity (WhatsApp, officer-assisted with the member confirming on their own device, or in-person with signed paper plus audit entry).

### 11.3 Confirmation flow
1. Application submitted → member gets "confirm you applied" (before committee time is spent).
2. After approval → member gets final terms and must confirm before disbursement.
3. The message is generic on lock screens; details appear on the confirmation page.
4. The confirmation page shows applicant, amount, tenor, monthly deduction, masked destination account, repayment responsibility, reference, expiry (default 24 hours, configurable), and the buttons **Confirm** and **This is not mine**. "This is not mine" asks "Are you sure?" and then opens a dispute.
5. Store: consent text version, terms hash, channel, message ID, timestamp, device/IP hint, decision.
6. Telegram bots can only message users who have started the bot; WhatsApp outbound messages outside the 24-hour window require approved templates.

### 11.4 Commands (Telegram first, same intents on WhatsApp)
`/balance /loan /schedule /statement /capacity /apply /pending /dispute /help`. `/capacity` explains in plain language how much the member can borrow and why. `/apply` collects amount and tenor, then hands off to the standard checks.

### 11.5 Privacy rules
No full account numbers, salary figures or ID numbers in messages. Ignore group chats. Never log OTPs. Rate-limit OTP and command frequency per member and per binding. Offer "stop messages" for non-critical notices. Tell members that chat history is stored on their device and the messaging platform.

### 11.6 WhatsApp
Start Meta business verification in Phase 1. Prepare utility templates (confirmation request, approved/rejected, disbursed, repayment posted, shortfall). Version templates in the database. Track cost per cooperative and show usage. Utility rates in Nigeria are roughly $0.0067 to $0.0101 per delivered message (confirm); marketing is several times higher and must not be used for transactional messages.

### 11.7 What chat does not fix
Stolen or swapped SIMs, unlocked phones, collusion between officers, members without smartphones. Mitigations: second-channel OTP, fast dispute and re-binding, independent disbursement authorization in the portal, printable statements and officer-assisted routes.

---

## 12. Security and compliance

### Security
- Staff authentication: Argon2id passwords, **mandatory TOTP MFA** for all staff roles, secure httpOnly cookies, CSRF protection, session expiry, rate limiting; passkeys later.
- Authorization: tenant check in the service layer **and** RLS; RLS, immutable ledger tables and separate roles are complementary, not substitutes. Privileged database owners are governed by audited procedures.
- Encryption at rest and in transit; documents (agreements, guarantor forms, ID copies) in private storage with expiring access links and retention rules.
- Dependency scanning, pinned versions, security headers, webhook signature checks, strict input validation, a pre-launch penetration test (even a self-run checklist) before the first cooperative goes live.
- Break-glass: operator access to tenant data only with a recorded reason, logged and visible to the cooperative.

### Compliance checklist (obtain written legal advice)
- **NDPA 2023 and GAID 2025** (in force since 19 September 2025): lawful basis and consent, DPA between Ajo and each cooperative (cooperative is controller, Ajo processor, CONFIRM), breach notification procedure, data-subject rights, DPIA, possible registration as a data controller/processor of major importance (with DPO and annual audit obligations) depending on scale, and **cross-border transfer** rules for Frankfurt-hosted data.
- **FCCPC DEON lending regulations 2025:** reportedly exclude duly registered cooperative societies; confirm for the cooperative and for Ajo's role as facilitator.
- State cooperative law and each cooperative's bye-laws.
- If a cooperative demands in-country hosting, evaluate a Nigeria-hosted Postgres deployment as an alternative data plane.

---

## 13. Frontend plan (officer portal, member page)

### Goals
Faster and clearer than anything an EXCO has used. Finance-grade calm, never cluttered.

### Stack
React 18 + TypeScript + Vite, Tailwind, shadcn/ui (Radix), TanStack Query and TanStack Table, React Hook Form + Zod, Recharts, generated API client from OpenAPI (`openapi-typescript` or `orval`), `date-fns`, Vitest + Testing Library, Playwright for end-to-end flows.

### Design principles
- **Money first:** tabular numerals, consistent `₦1,250,000.00` formatting, negative amounts clearly marked, no ambiguous rounding.
- **Every number explains itself:** click a balance or capacity to see the ledger lines or formula behind it.
- **Loan timeline:** a single vertical timeline per loan showing application, confirmations (with evidence), approvals, disbursement, repayments, disputes.
- **Speed:** command palette (Ctrl/Cmd-K), keyboard shortcuts, saved filters, instant search across members and loans, bulk actions where safe.
- **Safety in the UI:** maker-checker visible, destructive actions need a reason, no silent saves, clear "partial check" warnings.
- **Accessibility and inclusivity:** WCAG AA, readable on low-end laptops and slow networks, i18n-ready strings (English first).
- **Performance budgets:** initial JS under about 250 KB gzipped for the portal; **member page under about 100 KB**, works on 3G and low-end Android, no heavy fonts; skeleton states, no layout shifts.

### Officer portal screens (MVP)
1. **Dashboard:** pending approvals, open disputes, payroll exceptions, arrears, fraud alerts, notification failures.
2. **Members:** list and **Member 360** (savings, dues, loans, guarantees, salary history, consents, channel binding status, audit).
3. **Loan pipeline:** table and board by status; **Loan detail** with timeline, eligibility breakdown, approvals, evidence, documents.
4. **Approvals queue** for committee with side-by-side capacity explanation.
5. **Disbursement desk** for treasurer and authorizer (reference entry and independent confirmation).
6. **Ledger explorer:** filter by account, member, loan, date; reversal workflow.
7. **Payroll reconciliation workspace:** upload file, auto-match, review exceptions, allocate partial payments, sign off period.
8. **Audit log viewer** and **evidence pack** export for auditors.
9. **Settings:** cap percentage, savings multiple, interest method, products, templates, roles, binding codes.
10. **Messaging usage:** volume and estimated cost per period.
11. **Migration wizard:** import opening balances, show reconciliation report, EXCO sign-off.

### Member page (signed link, mobile-first)
Confirmation page, statement, capacity explainer. Minimal, fast, clear.

---

## 14. Notifications and scheduling

- `notifications` is a transactional **outbox**: rows created in the same transaction as the business event; the worker claims with `FOR UPDATE SKIP LOCKED`, sends, records attempts, backs off, dead-letters after N failures (treasurer sees a dead-letter view).
- Channel-neutral, **versioned** templates stored in the database.
- Scheduler runs as **one** process (or guards each job with an advisory lock plus idempotency key) so dues, schedules and reminders never double-run across Gunicorn workers.
- Reminders respect member preferences for non-critical messages; critical notices (confirmation required, dispute raised, binding created) are always sent.

---

## 15. Payroll reconciliation (core module)

1. Generate the expected deduction schedule per period and export it in the format the payroll office accepts (CONFIRM format).
2. Import the payroll result file (CSV/Excel). Validate, deduplicate, and map to members.
3. Auto-match expected vs actual; classify exceptions (short, over, missing, unknown member, duplicate).
4. Allocate partial payments by a documented waterfall (default: dues → interest → principal, CONFIRM).
5. Post to the ledger through journal entries; close the period with sign-off and a reconciliation report.

---

## 16. Data migration from the current system

Obtain an export (who owns the data? CONFIRM). Import opening balances, active loans and history into a **staging branch first**. Produce a reconciliation report (counts, balances per member, per product, totals). The EXCO signs off within an agreed tolerance before go-live. Without this, the pilot will not be trusted.

---

## 17. Observability, backups, disaster recovery

- Structured logs without personal data; Sentry for errors; uptime checks on API, webhooks and worker heartbeats; alerts for outbox backlog, dead letters, failed jobs, reconciliation mismatches, and unusual dispute rates.
- Targets (initial, adjust): recovery point objective under 24 hours via nightly dumps plus Neon restore; recovery time objective under 4 hours; monthly restore drill with a written result.
- Runbooks in `/docs/runbooks`: deploy, rollback, restore, rotate secrets, bot outage, WhatsApp template rejection, suspected breach, break-glass.

---

## 18. Testing and acceptance

### Layers
Unit tests for rules; property tests (Hypothesis) for ledger invariants (always balanced, reversals net to zero, no negative amounts); integration tests against a real Postgres (Neon branch or local container); concurrency tests; security tests; Playwright flows; replay tests for webhooks.

### Acceptance tests (must pass before pilot)
1. Staff, officer or admin cannot submit an application on behalf of a member without that member's confirmation.
2. A loan cannot be disbursed without the **borrower's** confirmation of the final terms, even when the borrower is the applicant.
3. If amount, tenor, beneficiary or account changes after confirmation, the confirmation is invalidated.
4. A member can dispute a loan from the bot; the loan becomes `disputed`, deductions pause, an alert is raised.
5. A binding code cannot be reused, expires, and a code for member A cannot bind member B.
6. A new or re-binding notifies the previous channel and follows the cooling-off rule.
7. OTP lockout and rate limits work.
8. Replaying a Telegram/WhatsApp webhook or a confirm tap never creates a second confirmation or ledger entry.
9. The bot ignores group chats and never shows another member's data, even with a forged callback payload.
10. Webhooks with a missing or invalid secret/signature are rejected.
11. Two simultaneous approvals for the same member cannot both succeed.
12. Savings backing an active loan cannot be withdrawn.
13. A guarantor's exposure counts when they apply for their own loan.
14. A salary reduction mid-loan raises a review flag when the instalment exceeds the cap.
15. With multiple API workers, scheduled jobs run exactly once per period.
16. Month-end and WAT/UTC boundary cases produce the correct payroll period.
17. The opening-balance import reconciles within the agreed tolerance and is signed off.
18. Every committed journal entry balances; any UPDATE/DELETE on ledger or audit tables fails for the app role.
19. RLS: a request with tenant A context cannot read tenant B data, even with crafted queries.
20. A restore from backup into a fresh Neon branch succeeds and passes a checksum/balance verification.
21. The audit hash chain verifies end to end; tampering is detected.
22. The confirmation page loads and works within the member-page performance budget on a throttled 3G profile.

---

## 19. Roadmap and delivery plan

Duration estimates are planning guesses for a solo developer working with an AI assistant. Re-estimate after Phase 1.

### Phase 0 — Policy, data, setup (about 1–2 weeks, partly parallel)
- Get EXCO answers to Section 21; fix the salary basis, cap, loan rules and approval chain.
- Define the typed policy version schema, approval/change log, tenant operating model and module records.
- Obtain a sample export of the existing system.
- Create the Neon project (Frankfurt), branches, roles; Docker Compose behind the existing Nginx on the Frankfurt VPS; CI with lint, type checks and tests.
- Start Meta business verification and legal consultations.
- **Exit:** policy sheet signed off, empty system deployed to staging with health checks.

### Phase 1 — Financial core (about 6–8 weeks)
Tenancy, auth with MFA, members, salary records, **double-entry ledger** with DB enforcement, dues and savings, cooperative policy version tables and maker-checker policy approval, loan products constrained by cooperative safeguards, applications, then eligibility engine with locking and partial unique index (recording the policy version used), approvals (recording the policy version used), manual disbursement with independent authorizer, schedule generation, audit log with hash chain, 5 fraud rules, minimal officer portal (members, loans, approvals, ledger explorer). MVP operating model is `loan_cooperative`.
- **Exit:** acceptance tests 1–3 (rules layer), 11–14, 18, 19, 21 pass; internal demo with seeded data.

### Phase 2 — Member channel and confirmations (about 3–4 weeks)
Telegram bot (aiogram 3) with identity binding, medium-risk commands, confirmation page, second-channel OTP, final-terms confirmation, dispute flow, notification outbox and worker.
- **Exit:** tests 1–10 and 22 pass end to end; first officers use it on staging.

### Phase 3 — Payroll reconciliation and migration (about 3–4 weeks)
Deduction schedule export, result import, auto-match, exceptions workspace, partial-payment allocation, migration wizard with reconciliation report.
- **Exit:** tests 15–17 pass; a dry run with real (masked) cooperative data reconciles.

### Phase 4 — WhatsApp and pilot (about 3–4 weeks)
Official WhatsApp adapter, approved templates, usage and cost report, PDF statements, bank-account change workflow with cooling-off, pilot with one cooperative and a small member group.
- **Exit:** pilot signed off by EXCO; incident and restore drills done (test 20).

### Phase 5 — SaaS readiness (about 4+ weeks)
Tenant administration UI, pricing and billing, onboarding playbook, year-end processes, external audit export, load and security testing, optional payment-provider disbursement, white-label bots, second cooperative. Add `thrift_society` after the MVP; add `rotating_ajo` only after validation with a real group.

### First working session checklist
1. Create repo skeleton per Section 8 and CI.
2. Create Neon project in `aws-eu-central-1`; set up `main/staging/dev` branches, roles and the two connection strings; note findings in `/docs/neon.md`.
3. [x] Migration 0001: cooperative tenants and RLS context helper.
4. [x] Migration 0002: accounts, journal entries and lines, balance trigger, immutability triggers, `post_journal_entry`, with integration and property tests (applied to the dev branch; exercised as `ajo_app`).
5. [x] Migration 0003: tenant directory, staff users and roles, MFA, sessions, recovery codes, rate limits, forced RLS and hash-chained audit events; bootstrap and verification CLI with integration tests (applied to the dev branch; exercised as `ajo_app`).
6. [x] Migration 0004: operating models and modules, typed policy versions with maker-checker approval, append-only policy change history and safeguarded loan products (applied to the dev branch; exercised as `ajo_app`).
7. Health endpoint, structured logging, Sentry, deployment to staging.

---

## 20. MVP default assumptions (all **CONFIRM with EXCO**; keep configurable)

| Item | Default |
|---|---|
| Active loans per member | One |
| Deduction cap | 70% of confirmed salary basis |
| Salary basis | Net salary (CONFIRM gross or net) |
| Max loan from savings | Configurable multiple of savings; placeholder 3× |
| Interest | Reducing balance, configurable (flat optional) |
| Confirmation expiry | 24 hours; OTP 5 minutes |
| Guarantors | Configurable count; placeholder 2 |
| Disbursement | Manual transfer, treasurer enters reference, a different authorizer confirms |
| Approval | Committee approves; a different person authorizes disbursement |
| Partial payment waterfall | Dues → interest → principal |
| Time zone for payroll periods | Africa/Lagos |

---

## 21. Open questions for the EXCO and lawyer

1. Is the 70% cap on gross or net salary, and which deductions count?
2. Can the cooperative see non-cooperative deductions or only its own?
3. Are third-party loans allowed? Who signs, and is the beneficiary liable on default?
4. How is maximum loan derived from savings (multiple, fixed cap, minimum remaining balance)?
5. Interest: charged? flat or reducing balance? rounding rules?
6. One active loan only, or top-ups and refinancing?
7. Guarantors: required, how many, liability?
8. Resignation, retirement, death: recovery order and when interest stops.
9. Who is in the approval chain and who sends money from the bank?
10. Which phone numbers are on record and are they verified?
11. How do payroll results arrive today (file, email, printed)? What format?
12. What does the existing app hold and who owns the data?
13. Do members mostly use WhatsApp, Telegram or both? (Survey before finalising channel order.)
14. Bye-laws or state cooperative rules restricting lending, records or member data?
15. Legal: is Ajo a processor, what are the cross-border transfer obligations for Frankfurt-hosted data, and is any Ajo activity regulated lending facilitation?

---

## 22. Commercial model (to test, not assume)

- Recurring **per active member per month** with a minimum floor per cooperative.
- One-time onboarding and **migration** fee.
- Messaging charged transparently as pass-through or tiered bundles; show each cooperative its usage and estimated cost.
- Data Processing Agreement and service terms signed with each cooperative before go-live.
- Sales story: *prevent unauthorized deductions, prove consent, explain every balance, pass an audit.*

---

## 23. Rules for writing code in this project

- No float money. No UPDATE/DELETE on ledger or audit. No logging of secrets or personal data.
- No financial logic outside the service layer. No vendor-specific database features.
- Every migration has a rehearsal on a Neon branch and a rollback note.
- Every feature ships with tests that map to Section 18.
- Prefer boring technology; add a dependency only with a written reason in an ADR.
- If this file and the code disagree, fix one of them in the same change.

---

## Change log

- **23 October 2026:** Updated D14 and deployment architecture for the existing Nginx reverse proxy; recorded Neon service and dev-branch restrictions; added policy and product configuration, operating models, module and ledger account requirements; replaced `cooperative_settings` with policy tables and reordered roadmap work so policy versions precede the eligibility engine.
- **24 October 2026:** Added and applied Alembic migration 0002 for the RLS-protected, append-only double-entry ledger, with invoker posting function, idempotency and reversal support; added `ajo_app` integration coverage and a Hypothesis balance property test. Lint, mypy and the full test suite passed.
- **24 October 2026:** Added and applied Alembic migration 0003 for tenant directory, staff authentication with mandatory TOTP MFA, forced-RLS auth tables, PostgreSQL lockout limits, sessions, recovery codes and append-only hash-chained audit events; added cooperative bootstrap and audit verification CLI commands plus integration coverage. Ruff, mypy and the full suite passed on the dev branch.
- **24 October 2026:** Added and applied Alembic migration 0004 for operating models/modules, typed cooperative policy versions, maker-checker approval, append-only policy change history and loan products constrained by cooperative safeguards; added role-checked policy, product and module endpoints with integration coverage as `ajo_app`. Ruff, formatting, mypy and the full suite passed (37 tests).
- **24 October 2026:** Reworked tests to use a disposable local Postgres 16 database and `ajo_app`, added unit/db/property markers and configurable Hypothesis profiles, consolidated coverage around the required invariants, added test database Compose and make targets, and updated CI to run `make test-all`.
- **24 October 2026:** Added migration 0005 and member, salary, bank-account, savings-lock and withdrawal-request data with forced tenant RLS; added default chart bootstrap, ledger-derived member balances, contribution and maker-checker withdrawal services, member/import endpoints, and the seven requested integration tests. Bank details expose only last four digits.
- **24 October 2026:** Added migration 0006 for forced-RLS loan applications, immutable hashed terms snapshots, loans and schedules, guarantees, committee approvals and fraud alerts; added integer-only WAT schedule and capacity calculations, eligibility and maker-checker loan workflow services, endpoints, and the requested calculation and workflow tests. Migration applied to dev; Ruff and mypy passed. Pytest was not run at the user's request.
