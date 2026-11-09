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

test("committee sees the review queue and a shared action destination", () => {
  const cards = dashboardCards(["committee"]);
  assert.ok(cards.some((card) => card.title === "Applications under review"));
  assert.deepEqual(cards.filter((card) => card.actionType).map((card) => card.href), ["/loans?action=needed"]);
});

test("auditor sees loan and disbursement status cards only", () => {
  const names = namesFor("auditor");
  assert.ok(names.includes("Loans: under review"));
  assert.ok(names.includes("Disbursements: awaiting authorization"));
  assert.ok(!names.includes("Payroll awaiting sign-off"));
  assert.ok(!names.includes("Ready to prepare"));
});

test("action dashboard cards link to the shared needs-action list", () => {
  const cards = dashboardCards(["admin", "officer", "treasurer", "committee", "auditor"]);
  for (const card of cards.filter((entry) => entry.actionType)) {
    assert.match(card.href, /(?:action=needed|mine=needed)/);
  }
  assert.ok(cards.some((card) => card.href === "/withdrawal-requests?mine=needed"));
  assert.ok(cards.some((card) => card.href === "/staff?action=needed"));
});
