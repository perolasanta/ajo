import assert from "node:assert/strict";
import test from "node:test";
import { dashboardCards } from "./dashboardPolicy.mjs";

const namesFor = (role) => dashboardCards([role]).map((card) => card.title);

test("admin sees loan status and disbursement action cards, but no blocked payroll card", () => {
  const names = namesFor("admin");
  assert.ok(names.includes("Loans: under review"));
  assert.ok(names.includes("Awaiting authorization"));
  assert.ok(names.includes("Awaiting confirmation"));
  assert.ok(!names.includes("Payroll awaiting sign-off"));
});

test("officer sees loan status cards but not disbursement cards", () => {
  const names = namesFor("officer");
  assert.ok(names.includes("Loans: under review"));
  assert.ok(!names.includes("Awaiting authorization"));
  assert.ok(!names.includes("Payroll in progress"));
});

test("treasurer sees the record-transfer card but not the unfilterable prepare queue", () => {
  const names = namesFor("treasurer");
  assert.ok(names.includes("Ready to record transfer"));
  assert.ok(!names.includes("Ready to prepare"));
  assert.ok(!names.includes("Payroll awaiting posting"));
});

test("committee sees only the review queue", () => {
  assert.deepEqual(namesFor("committee"), ["Applications under review"]);
});

test("auditor sees loan and disbursement status cards only", () => {
  const names = namesFor("auditor");
  assert.ok(names.includes("Loans: under review"));
  assert.ok(names.includes("Disbursements: awaiting authorization"));
  assert.ok(!names.includes("Payroll awaiting sign-off"));
  assert.ok(!names.includes("Ready to prepare"));
});

test("every dashboard card links to a status-filtered existing list", () => {
  for (const card of dashboardCards(["admin", "officer", "treasurer", "committee", "auditor"])) {
    assert.match(card.href, /status=/);
    assert.ok(["/loans", "/disbursements"].includes(card.href.split("?")[0]));
  }
});
