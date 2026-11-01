import assert from "node:assert/strict";
import test from "node:test";
import { canViewPayroll, payrollListState, payrollReadRoles } from "./payrollPolicy.mjs";

for (const role of ["officer", "treasurer", "admin"]) {
  test(`${role} can view payroll`, () => {
    assert.equal(canViewPayroll([role]), true);
  });
}

for (const role of ["committee", "auditor", "member"]) {
  test(`${role} cannot view payroll`, () => {
    assert.equal(canViewPayroll([role]), false);
  });
}

test("payroll list access is limited to the decision 1b read roles", () => {
  assert.deepEqual(payrollReadRoles, ["officer", "treasurer", "admin"]);
});

test("payroll list exposes loading and error states", () => {
  assert.equal(payrollListState({ isPending: true, isError: false }), "loading");
  assert.equal(payrollListState({ isPending: false, isError: true }), "error");
  assert.equal(payrollListState({ isPending: false, isError: false }), "loading");
});

test("payroll list distinguishes empty and populated results", () => {
  assert.equal(
    payrollListState({ isPending: false, isError: false, data: { items: [] } }),
    "empty",
  );
  assert.equal(
    payrollListState({ isPending: false, isError: false, data: { items: [{ run_id: "r1" }] } }),
    "results",
  );
});
