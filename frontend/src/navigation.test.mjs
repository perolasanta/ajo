import assert from "node:assert/strict";
import test from "node:test";
import { visibleNavigation } from "./navigation.js";
import { canDecideLoans, canSeeDestinationAccount, canSubmitLoans } from "./loanPolicy.mjs";

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
