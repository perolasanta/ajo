import assert from "node:assert/strict";
import test from "node:test";
import { loanSnapshotPresentation } from "./loanSnapshotPresentation.mjs";

const snapshot = {
  principal_kobo: 123456,
  annual_rate_bps: 1250,
  tenor_months: 6,
  product_id: "11111111-1111-4111-8111-111111111111",
  beneficiary_member_id: "22222222-2222-4222-8222-222222222222",
  destination_bank_account_id: "33333333-3333-4333-8333-333333333333",
  policy_version_id: "44444444-4444-4444-8444-444444444444",
  destination: {
    bank_code: "001",
    last4: "0010",
    account_name: "PRIVATE ACCOUNT NAME",
    status: "verified",
    verified: true,
  },
};

test("loan detail snapshot displays readable terms and never exposes raw identifiers or JSON keys", () => {
  const fields = loanSnapshotPresentation(snapshot, {
    productName: "Emergency loan",
    beneficiaryName: "Aisha Bello",
  });
  const visibleText = fields.map(({ label, value }) => `${label}: ${value}`).join(" | ");

  assert.deepEqual(fields, [
    { label: "Principal", value: "₦1,234.56" },
    { label: "Annual rate", value: "12.50%" },
    { label: "Tenor", value: "6 months" },
    { label: "Product name", value: "Emergency loan" },
    { label: "Beneficiary name", value: "Aisha Bello" },
    { label: "Destination", value: "001 · ••0010 (verified)" },
  ]);
  assert.doesNotMatch(visibleText, /[0-9a-f]{8}-[0-9a-f-]{27,}/i);
  assert.doesNotMatch(visibleText, /_kobo|_bps|product_id|member_id|account_id|policy_version_id/);
  assert.doesNotMatch(visibleText, /PRIVATE ACCOUNT NAME|account_name/);
});
