import assert from "node:assert/strict";
import test from "node:test";
import { disbursementRoles, visibleNavigation } from "./navigation.js";
import { canDecideLoans, canSeeDestinationAccount, canSubmitLoans } from "./loanPolicy.mjs";
import {
  canAuthorizeDisbursement,
  canConfirmDisbursementPaid,
  canFailDisbursement,
  canPrepareDisbursement,
  canRecordDisbursementTransfer,
  canReverseDisbursement,
  shouldExplainPreparedByCurrentUser,
} from "./disbursementPolicy.mjs";

const cases = [
  ["treasurer", true],
  ["auditor", true],
  ["officer", true],
  ["admin", true],
  ["committee", false],
];

for (const [role, expected] of cases) {
  test(`${role} ${expected ? "sees" : "does not see"} Members navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Members"), expected);
  });
}

const loanCases = [
  ["admin", true],
  ["officer", true],
  ["treasurer", true],
  ["committee", true],
  ["auditor", true],
  ["member", false],
];

for (const [role, expected] of [
  ["admin", true],
  ["treasurer", true],
  ["auditor", true],
  ["officer", false],
  ["committee", false],
  ["member", false],
]) {
  test(`${role} ${expected ? "sees" : "does not see"} Disbursements navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Disbursements"), expected);
  });
}

test("disbursement navigation allowlist contains only admin, treasurer and auditor", () => {
  assert.deepEqual(disbursementRoles, ["admin", "treasurer", "auditor"]);
});

for (const [role, expected] of [
  ["admin", true],
  ["auditor", true],
  ["officer", false],
  ["treasurer", false],
  ["committee", false],
  ["member", false],
]) {
  test(`${role} ${expected ? "sees" : "does not see"} Audit navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Audit"), expected);
  });
}

for (const [role, expected] of loanCases) {
  test(`${role} ${expected ? "sees" : "does not see"} Loans navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Loans"), expected);
  });
}

test("only admin and officer can submit loan applications", () => {
  assert.equal(canSubmitLoans(["admin"]), true);
  assert.equal(canSubmitLoans(["officer"]), true);
  assert.equal(canSubmitLoans(["committee"]), false);
  assert.equal(canSubmitLoans(["treasurer"]), false);
  assert.equal(canSubmitLoans(["auditor"]), false);
});

test("only committee can decide an application under review", () => {
  assert.equal(canDecideLoans(["committee"], "under_review"), true);
  assert.equal(canDecideLoans(["committee"], "approved"), false);
  assert.equal(canDecideLoans(["admin"], "under_review"), false);
});

test("bank details are visible only to bank-authorized roles", () => {
  assert.equal(canSeeDestinationAccount(["admin"]), true);
  assert.equal(canSeeDestinationAccount(["officer"]), true);
  assert.equal(canSeeDestinationAccount(["treasurer"]), true);
  assert.equal(canSeeDestinationAccount(["committee"]), false);
  assert.equal(canSeeDestinationAccount(["auditor"]), false);
});

test("disbursement actions respect role, workflow state and maker-checker separation", () => {
  const pending = {
    status: "pending_authorization",
    prepared_by: "maker",
    authorized_by: null,
    confirmed_by: null,
  };
  const authorized = { ...pending, status: "authorized", authorized_by: "admin-1" };
  const transferRecorded = { ...authorized, status: "transfer_recorded" };
  const paid = { ...transferRecorded, status: "paid", confirmed_by: "admin-1" };
  assert.equal(canPrepareDisbursement(["treasurer"]), true);
  assert.equal(canPrepareDisbursement(["admin"]), false);
  assert.equal(canAuthorizeDisbursement(["admin"], "checker", pending), true);
  assert.equal(canAuthorizeDisbursement(["admin"], "maker", pending), false);
  assert.equal(shouldExplainPreparedByCurrentUser("maker", pending), true);
  assert.equal(canRecordDisbursementTransfer(["treasurer"], "treasurer-1", authorized), true);
  assert.equal(canRecordDisbursementTransfer(["treasurer"], "admin-1", authorized), false);
  assert.equal(canRecordDisbursementTransfer(["admin"], "admin-2", authorized), false);
  assert.equal(canConfirmDisbursementPaid(["admin"], "admin-1", transferRecorded), true);
  assert.equal(canConfirmDisbursementPaid(["admin"], "admin-2", transferRecorded), false);
  assert.equal(canFailDisbursement(["treasurer"], "treasurer-1", authorized), true);
  assert.equal(canFailDisbursement(["treasurer"], "admin-1", authorized), false);
  assert.equal(canReverseDisbursement(["admin"], "admin-2", paid), true);
  assert.equal(canReverseDisbursement(["admin"], "admin-1", paid), false);
});
