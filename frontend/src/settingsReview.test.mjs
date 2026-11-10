import test from "node:test";
import assert from "node:assert/strict";
import {
  buildPolicyReviewRows,
  buildProductReviewRows,
  canApproveSettings,
} from "./settingsReview.mjs";

test("policy review contains exactly changed fields in human units", () => {
  const rows = buildPolicyReviewRows(
    {
      deduction_cap_bps: 7000,
      min_savings_kobo: 2500000,
      loan_to_savings_multiple_x100: 300,
      settings: { min_membership_months: 6 },
    },
    {
      deduction_cap_bps: 6500,
      min_savings_kobo: 3000000,
      loan_to_savings_multiple_x100: 400,
      settings: { min_membership_months: 12 },
    },
  );
  assert.deepEqual(rows.map((row) => row.label), [
    "Deduction cap",
    "Minimum savings",
    "Loan-to-savings multiple",
    "Minimum membership",
  ]);
  assert.deepEqual(rows.map((row) => [row.current, row.proposed]), [
    ["70%", "65%"],
    ["₦25,000.00", "₦30,000.00"],
    ["3x", "4x"],
    ["6 months", "12 months"],
  ]);
});

test("product review uses annual and monthly rate plus superseded values", () => {
  const rows = buildProductReviewRows(
    { annual_rate_bps: 3600, min_tenor_months: 3, max_tenor_months: 12 },
    { annual_rate_bps: 4800, min_tenor_months: 6, max_tenor_months: 18 },
  );
  assert.deepEqual(rows.map((row) => row.label), ["Rate", "Minimum term", "Maximum term"]);
  assert.equal(rows[0].proposed, "48% a year (4% a month)");
});

test("only an eligible different admin can approve settings", () => {
  assert.equal(canApproveSettings({ roles: ["admin"], userId: "a" }, "b"), true);
  assert.equal(canApproveSettings({ roles: ["officer"], userId: "a" }, "b"), false);
  assert.equal(canApproveSettings({ roles: ["admin"], userId: "a" }, "a"), false);
  assert.equal(canApproveSettings({ roles: ["auditor"], userId: "a" }, "b"), false);
});
