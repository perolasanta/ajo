const policyFieldDefinitions = [
  ["deduction_cap_bps", "Deduction cap", (value) => `${Number(value ?? 0) / 100}%`],
  [
    "min_savings_kobo",
    "Minimum savings",
    (value) =>
      `₦${(Number(value ?? 0) / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
  ],
  [
    "loan_to_savings_multiple_x100",
    "Loan-to-savings multiple",
    (value) => `${Number(value ?? 0) / 100}x`,
  ],
  ["max_active_loans", "Maximum active loans", (value) => `${value ?? "—"}`],
  ["guarantors_required", "Guarantors required", (value) => `${value ?? "—"}`],
  ["salary_basis", "Salary basis", (value) => `${value ?? "—"}`],
  ["interest_method", "Interest method", (value) => `${value ?? "—"}`],
  ["confirmation_expiry_minutes", "Confirmation expiry", (value) => `${value ?? "—"} minutes`],
  ["otp_validity_seconds", "OTP validity", (value) => `${value ?? "—"} seconds`],
  [
    "payroll_allocation_order",
    "Payroll allocation order",
    (value) => (Array.isArray(value) ? value.join(", ") : `${value ?? "—"}`),
  ],
  ["savings_backing_bps", "Savings backing", (value) => `${Number(value ?? 0) / 100}%`],
  ["min_membership_months", "Minimum membership", (value) => `${value ?? 0} months`],
  ["guarantors_must_be_active", "Active-member guarantors", (value) => String(value ?? true)],
];

function policyValue(policy, key) {
  if (
    key === "savings_backing_bps" ||
    key === "min_membership_months" ||
    key === "guarantors_must_be_active"
  ) {
    return policy?.settings?.[key];
  }
  return policy?.[key];
}

export function buildPolicyReviewRows(current, proposed) {
  return policyFieldDefinitions.flatMap(([key, label, format]) => {
    const before = policyValue(current, key);
    const after = policyValue(proposed, key);
    if (JSON.stringify(before) === JSON.stringify(after)) return [];
    return [{ key, label, current: format(before), proposed: format(after), changed: true }];
  });
}

const productFields = [
  [
    "annual_rate_bps",
    "Rate",
    (value) => `${Number(value ?? 0) / 100}% a year (${Number(value ?? 0) / 1200}% a month)`,
  ],
  [
    "min_amount_kobo",
    "Minimum amount",
    (value) =>
      `₦${(Number(value ?? 0) / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
  ],
  [
    "max_amount_kobo",
    "Maximum amount",
    (value) =>
      `₦${(Number(value ?? 0) / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
  ],
  ["min_tenor_months", "Minimum term", (value) => `${value ?? "—"} months`],
  ["max_tenor_months", "Maximum term", (value) => `${value ?? "—"} months`],
  ["fee_bps", "Fee", (value) => `${Number(value ?? 0) / 100}%`],
  ["guarantors_required", "Guarantors required", (value) => `${value ?? "—"}`],
  ["interest_method", "Interest method", (value) => `${value ?? "—"}`],
  ["name", "Product name", (value) => `${value ?? "—"}`],
];

export function buildProductReviewRows(current, proposed) {
  return productFields.flatMap(([key, label, format]) => {
    if (JSON.stringify(current?.[key]) === JSON.stringify(proposed?.[key])) return [];
    return [
      {
        key,
        label,
        current: format(current?.[key]),
        proposed: format(proposed?.[key]),
        changed: true,
      },
    ];
  });
}

export function canApproveSettings(session, proposerId) {
  return session.roles.includes("admin") && session.userId !== proposerId;
}

export function formatActorLabel(name, email, showEmail) {
  const display = name || (email ? "Unnamed user" : "Unknown user");
  return showEmail && email ? `${display} (${email})` : display;
}
