import assert from "node:assert/strict";
import test from "node:test";
import { eventsForDisbursement } from "./disbursementTimeline.js";

const prepared = {
  id: "d1",
  loan_id: "l1",
  application_id: "a1",
  member_no: "M1",
  full_name: "Member",
  amount_kobo: 1000,
  status: "pending_authorization",
  prepared_by: "user-1",
  authorized_by: null,
  transfer_reference: null,
  transfer_date: null,
  confirmed_by: null,
  failure_reason: null,
  reversal_reason: null,
  created_at: "2026-10-01T10:00:00Z",
  authorized_at: null,
  transfer_recorded_at: null,
  paid_at: null,
  failed_at: null,
  reversed_at: null,
};

test("timeline includes only workflow steps and timestamps returned by the API", () => {
  assert.deepEqual(
    eventsForDisbursement(prepared).map((item) => item.title),
    ["Prepared"],
  );
});

test("timeline displays transfer, paid confirmation, failure and reversal when present", () => {
  const item = {
    ...prepared,
    status: "reversed",
    authorized_by: "user-2",
    authorized_at: "2026-10-01T11:00:00Z",
    transfer_reference: "BANK-REF",
    transfer_recorded_at: "2026-10-01T12:00:00Z",
    paid_at: "2026-10-01T13:00:00Z",
    confirmed_by: "user-3",
    failed_at: "2026-10-02T10:00:00Z",
    failure_reason: "Rejected",
    reversed_at: "2026-10-03T10:00:00Z",
    reversal_reason: "Incorrect account",
  };
  const events = eventsForDisbursement(item);
  assert.deepEqual(
    events.map((entry) => entry.title),
    [
      "Prepared",
      "Authorized",
      "Transfer recorded",
      "Paid confirmed",
      "Transfer failed",
      "Payment reversed",
    ],
  );
  assert.equal(events[2].evidence, "BANK-REF");
  assert.equal(events[4].evidence, "Rejected");
  assert.equal(events[5].evidence, "Incorrect account");
});
