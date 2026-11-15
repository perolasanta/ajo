import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./pages/ShareRegisterPage.tsx", import.meta.url), "utf8");

test("share register presents member holdings and cooperative totals", () => {
  assert.match(page, /getShareRegister/);
  assert.match(page, /Total shares/);
  assert.match(page, /Members holding shares/);
  assert.match(page, /Shares held/);
  assert.match(page, /Value at current price/);
  assert.match(page, /Last purchase/);
});

test("share register supports member-name search and paginated read-only results", () => {
  assert.match(page, /Search member name/);
  assert.match(page, /setTimeout/);
  assert.match(page, /Previous/);
  assert.match(page, /Next/);
  assert.doesNotMatch(page, /requestSharePurchase|useMutation|method:\s*["']POST/);
});
