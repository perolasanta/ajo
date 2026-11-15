# Overdue cash alerts and no-confirmer warning progress
STATE: Step 1 plan recorded; implementation not started.

## Checklist
- [x] 1. Read scheduler, overdue-cash rule, ACTION_SPECS, shared eligibility, and newest migration; record implementation plan.
- [ ] 2. Add `cash_deposit_overdue`, the new notification-kind migration, and kind-parity tests.
- [ ] 3. Add tests first, then the overdue-cash scheduler job, resilient interval loop, and `--run-once` CLI.
- [ ] 4. Document the VPS command and product decision/follow-up.
- [ ] 5. Add backend no-eligible-confirmer flags and tests for payment queues.
- [ ] 6. Add the frontend warning and tests.
- [ ] 7. Run the required final checks, review GitHub CI, and keep the PR draft.

## Plan

### Overdue rule and scheduler
Reuse the existing Payments cash-awaiting-deposit projection rule: a received cash receipt is overdue only when its WAT received date plus the effective `cash_banking_days` is strictly before the current/as-of WAT date. Refactor the existing projection to expose one shared query/helper so both the page and scheduled job consume the same rule; do not duplicate the threshold logic. Preserve tenant scoping and use the configured `DATABASE_URL_SCHEDULER` role (`ajo_scheduler`) for per-cooperative transactions with `app.cooperative_id` set locally so RLS remains active.

Add a setting for the overdue-cash interval, defaulting to 60 minutes. Keep the scheduler session advisory lock and replace the placeholder sleep loop with a resilient job loop. Process cooperatives independently, continue after a cooperative/job error, and log only counts plus safe error classes—never receipt/member names or amounts. Implement `python -m app.scheduler --run-once [--as-of YYYY-MM-DD] [--dry-run]`; dry-run must not enqueue and must print only per-cooperative counts.

### Eligibility and notifications
Add `cash_deposit_overdue` to `ACTION_SPECS` and to the notifications action-kind CHECK in a new migration after 0039. Route all recipients through the shared action-eligibility/recipient function: active treasurers who recorded none of the receipts that are overdue for that cooperative/date. Use the standard in-app action queue/badge and enabled email/linked-Telegram channels. Keep alert payloads generic (kind and direct payments link only). Deduplicate independently per channel, per cooperative, per recipient, per WAT day while overdue cash remains; do not create one notification per receipt. Add kind-parity/structural tests and ensure every registered action has its enqueue callsite.

### No-eligible-confirmer warning
Start with payment queues (pending bank transfers and cash-deposit verification) and derive the flag from the shared eligibility function, not new role logic. Show it to admin as a read-only setup warning. If extending every other maker-checker queue is not a small, low-risk slice, keep scope to payments and document the remaining queues as a follow-up in `docs/decisions.md`. Do not add notification kinds or change confirmation authority.

### Migration and checks
The newest migration on main is 0039; create the next migration without editing any existing migration. Write the requested tests before implementation. Avoid Docker/image pulls and the full backend suite; rely on targeted checks and GitHub CI for the judge. Update the runbook with an exact in-container `--run-once --as-of ... --dry-run` command.
