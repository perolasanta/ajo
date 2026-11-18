import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { SHARE_SAVINGS_ERROR, sharePurchaseRequestState } from "./sharePurchasePolicy.mjs";

const page = readFileSync(new URL("./pages/SharePurchasesPage.tsx", import.meta.url), "utf8");

test("share request allows an exact savings match and blocks an over-balance amount", () => {
  assert.deepEqual(sharePurchaseRequestState(5, 10, 5_000), {
    amountKobo: 5_000,
    canSubmit: true,
    overSavings: false,
  });
  assert.deepEqual(sharePurchaseRequestState(6, 10, 5_000), {
    amountKobo: 6_000,
    canSubmit: false,
    overSavings: true,
  });
});

test("share request blocks zero, negative, fractional, and unsafe amounts", () => {
  for (const sharesCount of [0, -1, 1.5, Number.MAX_SAFE_INTEGER]) {
    assert.equal(sharePurchaseRequestState(sharesCount, 10, 5_000).canSubmit, false);
  }
});

test("form shows the server-matching savings error and disables invalid submission", () => {
  assert.equal(
    SHARE_SAVINGS_ERROR,
    "Requested share purchase amount exceeds the member's available savings",
  );
  assert.match(page, /SHARE_SAVINGS_ERROR/);
  assert.match(page, /disabled=\{create\.isPending \|\| !memberId \|\| !requestState\.canSubmit\}/);
});
