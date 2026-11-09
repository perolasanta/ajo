import assert from "node:assert/strict";
import test from "node:test";
import { formatRate } from "./loanRate.mjs";

test("formats annual bps as annual and monthly rates", () => {
  assert.equal(formatRate(3600), "36.00% a year (3.00% a month)");
});
