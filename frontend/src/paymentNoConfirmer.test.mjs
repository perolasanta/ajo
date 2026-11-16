import assert from "node:assert/strict";
import test from "node:test";
import { noEligibleConfirmerWarning, pendingNoConfirmerSummary } from "./paymentNoConfirmer.mjs";

test("no-confirmer warning tells staff how to unblock a payment", () => {
  assert.match(noEligibleConfirmerWarning, /No other eligible person can confirm this/i);
  assert.match(noEligibleConfirmerWarning, /Add another officer or treasurer/i);
  assert.equal(pendingNoConfirmerSummary(1), "1 pending item has no eligible confirmer.");
  assert.equal(pendingNoConfirmerSummary(2), "2 pending items have no eligible confirmer.");
});
