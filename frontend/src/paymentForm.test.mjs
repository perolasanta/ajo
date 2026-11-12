import assert from "node:assert/strict";
import test from "node:test";
import {
  buildPaymentPayload,
  canRecordPayments,
  nairaToKobo,
  validatePaymentDraft,
} from "./paymentForm.mjs";

const validDraft = {
  memberId: "member-1",
  channel: "cash",
  purpose: "savings",
  amountNaira: "120.05",
  providerReference: "",
  receivedOn: "2026-11-11",
  loanId: "",
  loanStatus: "",
  deductionsPaused: false,
  reason: "",
  roles: ["officer"],
};

test("naira input converts exactly to integer kobo without float rounding", () => {
  assert.equal(nairaToKobo("0.01"), 1);
  assert.equal(nairaToKobo("120.05"), 12005);
  assert.equal(nairaToKobo("1"), 100);
  for (const value of ["0", "-1", "1.001", "abc", "90071992547409.92"]) {
    assert.throws(() => nairaToKobo(value));
  }
});

test("only officers and treasurers can record payments", () => {
  assert.equal(canRecordPayments(["officer"]), true);
  assert.equal(canRecordPayments(["treasurer"]), true);
  for (const role of ["admin", "auditor", "committee", "member"]) {
    assert.equal(canRecordPayments([role]), false);
  }
});

test("transfer reference and loan selection are required by form validation", () => {
  assert.match(
    validatePaymentDraft({ ...validDraft, channel: "bank_transfer" }),
    /bank reference/i,
  );
  assert.match(
    validatePaymentDraft({ ...validDraft, purpose: "loan_repayment" }),
    /choose a loan/i,
  );
  assert.equal(
    validatePaymentDraft({
      ...validDraft,
      channel: "bank_transfer",
      providerReference: "TRX-123",
    }),
    null,
  );
});

test("disputed or paused loan repayments use the treasurer-only reason path", () => {
  const disputed = {
    ...validDraft,
    purpose: "loan_repayment",
    loanId: "loan-1",
    loanStatus: "disputed",
  };
  assert.match(validatePaymentDraft(disputed), /treasurer/i);
  assert.equal(validatePaymentDraft({ ...disputed, roles: ["treasurer"] }), /reason/i);
  assert.equal(
    validatePaymentDraft({ ...disputed, roles: ["treasurer"], reason: "Reviewed manually" }),
    null,
  );
  assert.match(
    validatePaymentDraft({
      ...disputed,
      loanStatus: "active",
      deductionsPaused: true,
      roles: ["officer"],
    }),
    /treasurer/i,
  );
});

test("request payload uses selected member, scoped loan, exact kobo and bank reference", () => {
  const payload = buildPaymentPayload({
    ...validDraft,
    channel: "bank_transfer",
    purpose: "loan_repayment",
    providerReference: " TRX-123 ",
    loanId: "loan-1",
    loanStatus: "disputed",
    roles: ["treasurer"],
    reason: "Reviewed manually",
  });
  assert.deepEqual(payload, {
    member_id: "member-1",
    channel: "bank_transfer",
    purpose: "loan_repayment",
    loan_id: "loan-1",
    amount_kobo: 12005,
    provider_reference: "TRX-123",
    received_on: "2026-11-11",
    fee_kobo: 0,
    fee_bearer: "member",
    reason: "Reviewed manually",
  });
});
