import test from "node:test";
import assert from "node:assert/strict";
import { visibleNavigation, getRouteDecision } from "./navigation.js";
import {
  amountNairaToKobo,
  annualPercentToBps,
  loanMultipleToX100,
  monthlyPercentToAnnualBps,
  roundNairaToKobo,
} from "./cooperativeSettings.mjs";

test("Settings navigation is available only to officers and admins", () => {
  assert.ok(visibleNavigation(["officer"]).some((item) => item.name === "Settings"));
  assert.ok(visibleNavigation(["admin"]).some((item) => item.name === "Settings"));
  for (const role of ["committee", "treasurer", "auditor"]) {
    assert.ok(!visibleNavigation([role]).some((item) => item.name === "Settings"));
    assert.equal(getRouteDecision([role], "/settings").allowed, false);
  }
});

test("policy values convert from human units without binary floating-point money", () => {
  assert.equal(loanMultipleToX100("3.00"), 300);
  assert.equal(annualPercentToBps("36"), 3600);
  assert.equal(amountNairaToKobo("1250"), 125000);
  assert.equal(roundNairaToKobo("12.345"), 1235);
});

test("monthly interest is annualized into the backend's basis-point rate", () => {
  assert.equal(monthlyPercentToAnnualBps("3.00"), 3600);
});
