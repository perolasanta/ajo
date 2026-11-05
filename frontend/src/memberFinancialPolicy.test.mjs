import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  canApproveWithdrawal,
  canRecordContribution,
  canRequestWithdrawal,
  canSeeMemberFinancialActions,
} from "./memberFinancialPolicy.js";

const cases = [
  ["admin", ["admin"], false, false, false, false],
  ["officer", ["officer"], true, true, false, true],
  ["treasurer", ["treasurer"], true, true, true, true],
  ["committee", ["committee"], false, false, false, false],
  ["auditor", ["auditor"], false, false, false, false],
  ["admin+treasurer", ["admin", "treasurer"], true, true, true, true],
];

for (const [label, roles, contribution, request, approve, visible] of cases) {
  test(`${label} financial action visibility follows the owner-approved matrix`, () => {
    assert.equal(canRecordContribution(roles), contribution);
    assert.equal(canRequestWithdrawal(roles), request);
    assert.equal(canApproveWithdrawal(roles), approve);
    assert.equal(canSeeMemberFinancialActions(roles), visible);
  });
}

test("member detail uses role policy and the design prototype has no mock posting action", () => {
  const memberPage = readFileSync(new URL("./pages/MemberDetailPage.tsx", import.meta.url), "utf8");
  const designPage = readFileSync(new URL("./pages/DesignPage.tsx", import.meta.url), "utf8");
  assert.match(memberPage, /canSeeMemberFinancialActions/);
  assert.doesNotMatch(designPage, /Record contribution/);
});
