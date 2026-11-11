import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  approveSettingsLoanProduct,
  decidePolicy,
  getPolicyVersions,
  getSettingsLoanProducts,
  getNotificationSummary,
  rejectPolicy,
  rejectSettingsLoanProduct,
  request,
  submitLoanProductProposal,
  submitPolicyProposal,
  type SettingsLoanProduct,
  type SettingsPolicyVersion,
} from "../api/client";
import {
  amountNairaToKobo,
  annualPercentToBps,
  loanMultipleToX100,
  monthlyPercentToAnnualBps,
} from "../cooperativeSettings.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import {
  buildPolicyReviewRows,
  buildProductReviewRows,
  canApproveSettings,
  formatActorLabel as actorLabel,
} from "../settingsReview.mjs";

type Session = { user_id: string; roles: string[] };
type EffectivePolicy = Omit<SettingsPolicyVersion, "status">;
type FieldDef = {
  key: string;
  label: string;
  help: string;
  suffix?: string;
  type?: string;
  min?: number;
  max?: number;
  options?: string[];
};

const policyFields: FieldDef[] = [
  {
    key: "salary_basis",
    label: "Salary basis",
    help: "Salary figure used for deduction-cap checks.",
    options: ["net", "gross"],
  },
  {
    key: "deduction_cap_percent",
    label: "Deduction cap",
    help: "Maximum share of salary available for deductions.",
    suffix: "%",
  },
  {
    key: "max_active_loans",
    label: "Maximum active loans",
    help: "How many active loans a member may hold.",
  },
  {
    key: "guarantors_required",
    label: "Guarantors required",
    help: "Minimum number of guarantors per loan.",
  },
  {
    key: "min_savings_naira",
    label: "Minimum savings",
    help: "Minimum savings balance required.",
    suffix: "₦",
  },
  {
    key: "loan_to_savings_x",
    label: "Loan-to-savings multiple",
    help: "Maximum loan amount as a multiple of savings.",
    suffix: "x",
  },
  {
    key: "interest_method",
    label: "Interest method",
    help: "How loan interest is calculated.",
    options: ["reducing_balance", "flat"],
  },
  {
    key: "payroll_allocation_order",
    label: "Payroll allocation order",
    help: "Order in which payroll deductions are allocated.",
    type: "text",
  },
  {
    key: "confirmation_expiry_minutes",
    label: "Confirmation expiry",
    help: "Minutes before a borrower confirmation expires.",
    suffix: "minutes",
  },
  {
    key: "otp_validity_seconds",
    label: "OTP validity",
    help: "Seconds an OTP remains valid.",
    suffix: "seconds",
  },
  {
    key: "cash_banking_days",
    label: "Cash banking window (days)",
    help: "Cash receipts must be deposited within this many calendar days of receipt.",
    type: "number",
    min: 1,
    max: 30,
  },
  {
    key: "savings_backing_percent",
    label: "Savings backing",
    help: "Share of the loan principal backed by savings.",
    suffix: "%",
  },
  {
    key: "min_membership_months",
    label: "Minimum membership",
    help: "Months a member must belong before applying.",
    suffix: "months",
  },
  {
    key: "guarantors_must_be_active",
    label: "Active-member guarantors",
    help: "Require guarantors to be active cooperative members.",
    options: ["true", "false"],
  },
];

const todayWAT = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Lagos" }).format(new Date());
const displayWAT = (value?: string | null) =>
  value
    ? new Intl.DateTimeFormat("en-NG", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Africa/Lagos",
      }).format(new Date(value)) + " WAT"
    : "—";
const errorText = (error: unknown) =>
  error instanceof ApiError ? error.message : "Please try again.";
const label = "block text-sm font-medium";
const input =
  "mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 py-2 text-sm";
const card = "rounded-card border border-border bg-surface p-4 sm:p-5";

function policyValues(policy?: EffectivePolicy | null): Record<string, string> {
  if (!policy) return {};
  const settings = policy.settings ?? {};
  return {
    salary_basis: policy.salary_basis,
    deduction_cap_percent: (policy.deduction_cap_bps / 100).toFixed(2),
    max_active_loans: String(policy.max_active_loans),
    guarantors_required: String(policy.guarantors_required),
    min_savings_naira: (policy.min_savings_kobo / 100).toFixed(2),
    loan_to_savings_x: (policy.loan_to_savings_multiple_x100 / 100).toFixed(2),
    interest_method: policy.interest_method,
    payroll_allocation_order: (policy.payroll_allocation_order ?? []).join(", "),
    confirmation_expiry_minutes: String(policy.confirmation_expiry_minutes),
    otp_validity_seconds: String(policy.otp_validity_seconds),
    cash_banking_days: String(policy.cash_banking_days),
    savings_backing_percent: (Number(settings.savings_backing_bps ?? 0) / 100).toFixed(2),
    min_membership_months: String(settings.min_membership_months ?? 0),
    guarantors_must_be_active: String(settings.guarantors_must_be_active ?? true),
  };
}

function PolicyFields({
  values,
  onChange,
}: {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {policyFields.map((field) => (
        <label className={label} key={field.key}>
          {field.label}
          {field.options ? (
            <Select
              className={input}
              value={values[field.key] ?? ""}
              onChange={(event) => onChange(field.key, event.target.value)}
            >
              {field.options.map((option) => (
                <option key={option} value={option}>
                  {option.replaceAll("_", " ")}
                </option>
              ))}
            </Select>
          ) : (
            <input
              className={input}
              type={field.type ?? "text"}
              min={field.min}
              max={field.max}
              inputMode={field.type ? undefined : "decimal"}
              value={values[field.key] ?? ""}
              onChange={(event) => onChange(field.key, event.target.value)}
              required
            />
          )}
          <span className="mt-1 block text-xs font-normal text-muted">
            {field.help}
            {field.suffix ? ` (${field.suffix})` : ""}
          </span>
        </label>
      ))}
    </div>
  );
}

export default function CooperativeSettingsPage() {
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const isAdmin = session.data?.roles.includes("admin") ?? false;
  const [tab, setTab] = useState<"policy" | "products" | "approval">(() =>
    new URLSearchParams(window.location.search).get("tab") === "approval" ? "approval" : "policy",
  );
  const [values, setValues] = useState<Record<string, string>>({});
  const [product, setProduct] = useState({
    name: "",
    minAmount: "",
    maxAmount: "",
    minTerm: "1",
    maxTerm: "12",
    monthlyRate: "3.00",
    annualRate: "36.00",
    interestMethod: "reducing_balance",
    feePercent: "0.00",
    guarantors: "1",
    effectiveOn: todayWAT(),
    reason: "",
    supersedesId: "",
  });
  const [policyDate, setPolicyDate] = useState(todayWAT());
  const [policyReason, setPolicyReason] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const policyQuery = useQuery({
    queryKey: ["policy-versions"],
    queryFn: getPolicyVersions,
    enabled: Boolean(
      session.data &&
      (session.data.roles.includes("admin") || session.data.roles.includes("officer")),
    ),
    retry: false,
  });
  const policy = useQuery({
    queryKey: ["effective-policy", policyDate],
    queryFn: () => request<EffectivePolicy>(`/policies/effective?on_date=${policyDate}`),
    enabled: Boolean(
      session.data &&
      (session.data.roles.includes("admin") || session.data.roles.includes("officer")),
    ),
    retry: false,
  });
  const productsQuery = useQuery({
    queryKey: ["settings-loan-products"],
    queryFn: getSettingsLoanProducts,
    enabled: Boolean(
      session.data &&
      (session.data.roles.includes("admin") || session.data.roles.includes("officer")),
    ),
    retry: false,
  });
  const summary = useQuery({ queryKey: ["notification-summary"], queryFn: getNotificationSummary });
  const settingsActionCount =
    (summary.data?.settings?.policy_approval ?? 0) +
    (summary.data?.settings?.product_approval ?? 0);
  useEffect(() => {
    if (settingsActionCount > 0 && tab === "policy") setTab("approval");
  }, [settingsActionCount, tab]);
  const currentValues = useMemo(() => policyValues(policy.data), [policy.data]);
  const formValues = Object.keys(values).length ? values : currentValues;
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["policy-versions"] });
    void queryClient.invalidateQueries({ queryKey: ["effective-policy"] });
    void queryClient.invalidateQueries({ queryKey: ["settings-loan-products"] });
    void queryClient.invalidateQueries({ queryKey: ["loan-products"] });
  };
  const proposePolicy = useMutation({
    mutationFn: () =>
      submitPolicyProposal({
        effective_from: effectiveFrom,
        salary_basis: formValues.salary_basis,
        deduction_cap_bps: annualPercentToBps(formValues.deduction_cap_percent),
        max_active_loans: Number(formValues.max_active_loans),
        guarantors_required: Number(formValues.guarantors_required),
        min_savings_kobo: amountNairaToKobo(formValues.min_savings_naira),
        loan_to_savings_multiple_x100: loanMultipleToX100(formValues.loan_to_savings_x),
        interest_method: formValues.interest_method,
        payroll_allocation_order: formValues.payroll_allocation_order
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean),
        confirmation_expiry_minutes: Number(formValues.confirmation_expiry_minutes),
        otp_validity_seconds: Number(formValues.otp_validity_seconds),
        cash_banking_days: Number(formValues.cash_banking_days),
        settings: {
          savings_backing_bps: annualPercentToBps(formValues.savings_backing_percent),
          min_membership_months: Number(formValues.min_membership_months),
          guarantors_must_be_active: formValues.guarantors_must_be_active === "true",
        },
        reason: policyReason.trim(),
      }),
    onSuccess: () => {
      setPolicyReason("");
      invalidate();
    },
  });
  const proposeProduct = useMutation({
    mutationFn: () => {
      const code = product.supersedesId
        ? productsQuery.data?.find((item) => item.id === product.supersedesId)?.code
        : product.name
            .toUpperCase()
            .replace(/[^A-Z0-9]+/g, "_")
            .replace(/^_|_$/g, "")
            .slice(0, 50);
      return submitLoanProductProposal({
        code: code || "PRODUCT",
        name: product.name.trim(),
        interest_method: product.interestMethod,
        annual_rate_bps: monthlyPercentToAnnualBps(product.monthlyRate),
        min_amount_kobo: amountNairaToKobo(product.minAmount),
        max_amount_kobo: amountNairaToKobo(product.maxAmount),
        min_tenor_months: Number(product.minTerm),
        max_tenor_months: Number(product.maxTerm),
        fee_bps: annualPercentToBps(product.feePercent),
        guarantors_required: Number(product.guarantors),
        supersedes_id: product.supersedesId || null,
        effective_on: product.effectiveOn,
        reason: product.reason.trim(),
      });
    },
    onSuccess: () => {
      setProduct((old) => ({ ...old, name: "", reason: "", supersedesId: "" }));
      invalidate();
    },
  });
  const policyAction = useMutation({
    mutationFn: ({
      id,
      action,
      reason,
      effective_to,
    }: {
      id: string;
      action: "approve" | "supersede";
      reason: string;
      effective_to?: string;
    }) => decidePolicy(id, action, action === "approve" ? { reason } : { reason, effective_to }),
    onSuccess: invalidate,
  });
  const approveProduct = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      approveSettingsLoanProduct(id, reason),
    onSuccess: invalidate,
  });
  const rejectPolicyMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => rejectPolicy(id, reason),
    onSuccess: invalidate,
  });
  const rejectProduct = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      rejectSettingsLoanProduct(id, reason),
    onSuccess: invalidate,
  });

  if (session.isPending) return <Skeleton className="h-60" />;
  if (!session.data || session.isError)
    return (
      <EmptyState title="Settings unavailable" detail="Sign in with an officer or admin account." />
    );
  if (!session.data.roles.some((role) => ["officer", "admin"].includes(role)))
    return (
      <EmptyState
        title="Access denied"
        detail="Settings are available only to cooperative officers and admins."
      />
    );
  const groupedProducts = new Map<string, SettingsLoanProduct[]>();
  for (const item of productsQuery.data ?? [])
    groupedProducts.set(item.code, [...(groupedProducts.get(item.code) ?? []), item]);
  const changed = policyFields.filter(
    (field) => currentValues[field.key] !== formValues[field.key],
  );

  return (
    <>
      <PageHeader title="Cooperative settings" breadcrumb="Policies and loan products" />
      <div className="mb-5 flex gap-2" role="tablist" aria-label="Settings sections">
        <Button
          role="tab"
          aria-selected={tab === "policy"}
          variant={tab === "policy" ? "primary" : "secondary"}
          onClick={() => setTab("policy")}
        >
          Policies
        </Button>
        <Button
          role="tab"
          aria-selected={tab === "approval"}
          variant={tab === "approval" ? "primary" : "secondary"}
          onClick={() => setTab("approval")}
        >
          Needs approval{settingsActionCount ? ` (${settingsActionCount})` : ""}
        </Button>
        <Button
          role="tab"
          aria-selected={tab === "products"}
          variant={tab === "products" ? "primary" : "secondary"}
          onClick={() => setTab("products")}
        >
          Loan products
        </Button>
      </div>
      {tab === "approval" && (
        <SettingsApprovalTab
          policies={policyQuery.data ?? []}
          products={productsQuery.data ?? []}
          effective={policy.data}
          userId={session.data.user_id}
          isAdmin={isAdmin}
          pending={
            policyAction.isPending ||
            approveProduct.isPending ||
            rejectPolicyMutation.isPending ||
            rejectProduct.isPending
          }
          onApprovePolicy={(id, reason) => policyAction.mutate({ id, action: "approve", reason })}
          onRejectPolicy={(id, reason) => rejectPolicyMutation.mutate({ id, reason })}
          onApproveProduct={(id, reason) => approveProduct.mutate({ id, reason })}
          onRejectProduct={(id, reason) => rejectProduct.mutate({ id, reason })}
        />
      )}
      {tab === "policy" && (
        <>
          <section className={`${card} mb-6`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="font-serif text-xl font-semibold">Currently effective policy</h2>
                <p className="mt-1 text-sm text-muted">
                  Effective on the selected WAT calendar date.
                </p>
              </div>
              <label className={label}>
                View date (WAT)
                <input
                  className={input}
                  type="date"
                  value={policyDate}
                  onChange={(event) => setPolicyDate(event.target.value)}
                />
              </label>
            </div>
            {policy.isPending && <Skeleton className="mt-5 h-24" />}
            {policy.isError && (
              <p role="alert" className="mt-4 text-sm text-danger">
                {errorText(policy.error)}
              </p>
            )}
            {policy.data && (
              <div className="mt-4">
                <p className="text-sm">
                  Version {policy.data.version} · {policy.data.effective_from} onward
                </p>
                <div className="mt-3">
                  <PolicyFields values={currentValues} onChange={() => undefined} />
                </div>
              </div>
            )}
          </section>
          <section className={`${card} mb-6`}>
            <h2 className="font-serif text-xl font-semibold">Propose new policy</h2>
            <p className="mt-1 text-sm text-muted">
              Starts with current values. A different admin must approve; proposers cannot approve
              their own proposal.
            </p>
            <form
              className="mt-4 space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (window.confirm("Submit this policy proposal for independent admin approval?"))
                  proposePolicy.mutate();
              }}
            >
              <PolicyFields
                values={formValues}
                onChange={(key, value) =>
                  setValues((old) => ({ ...currentValues, ...old, [key]: value }))
                }
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <label className={label}>
                  Effective from (WAT)
                  <input
                    className={input}
                    type="date"
                    required
                    value={effectiveFrom}
                    onChange={(event) => setEffectiveFrom(event.target.value)}
                  />
                </label>
                <label className={label}>
                  Reason
                  <input
                    className={input}
                    required
                    maxLength={1000}
                    value={policyReason}
                    onChange={(event) => setPolicyReason(event.target.value)}
                  />
                </label>
              </div>
              <div className="rounded-control bg-[#F7F5EF] p-3 text-sm">
                <strong>Change summary</strong>
                {changed.length ? (
                  <ul className="mt-2 list-disc pl-5">
                    {changed.map((field) => (
                      <li key={field.key}>
                        {field.label}: {currentValues[field.key] || "—"} →{" "}
                        {formValues[field.key] || "—"}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-muted">No changes from the current policy.</p>
                )}
              </div>
              {proposePolicy.isError && (
                <p className="text-sm text-danger" role="alert">
                  {errorText(proposePolicy.error)}
                </p>
              )}
              {proposePolicy.isSuccess && (
                <p className="text-sm text-success" role="status">
                  Proposal submitted; it is waiting for another admin.
                </p>
              )}
              <Button
                type="submit"
                loading={proposePolicy.isPending}
                disabled={!policy.data || !effectiveFrom}
              >
                Submit policy proposal
              </Button>
            </form>
          </section>
          <section className={card}>
            <h2 className="font-serif text-xl font-semibold">Policy version history</h2>
            {policyAction.isError && (
              <p role="alert" className="mt-3 text-sm text-danger">
                {errorText(policyAction.error)}
              </p>
            )}
            {policyQuery.isPending && <Skeleton className="mt-4 h-32" />}
            {policyQuery.isError && (
              <p role="alert" className="mt-4 text-sm text-danger">
                {errorText(policyQuery.error)}
              </p>
            )}
            {!policyQuery.isPending && policyQuery.data?.length === 0 && (
              <EmptyState
                title="No policy history"
                detail="Approved and proposed versions will appear here."
              />
            )}
            {policyQuery.data?.map((version) => (
              <PolicyHistoryItem
                key={version.id}
                item={version}
                isAdmin={isAdmin}
                pending={policyAction.isPending}
                onAction={(action, reason, end) =>
                  policyAction.mutate({ id: version.id, action, reason, effective_to: end })
                }
              />
            ))}
          </section>
        </>
      )}
      {tab === "products" && (
        <>
          <section className={`${card} mb-6`}>
            <h2 className="font-serif text-xl font-semibold">Propose loan product</h2>
            <p className="mt-1 text-sm text-muted">
              Approved versions are immutable. Create a superseding version to replace an existing
              product.
            </p>
            <form
              className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (
                  window.confirm(
                    "Submit this loan product proposal for independent admin approval?",
                  )
                )
                  proposeProduct.mutate();
              }}
            >
              <label className={label}>
                Product name
                <input
                  className={input}
                  required
                  maxLength={160}
                  value={product.name}
                  onChange={(e) => setProduct({ ...product, name: e.target.value })}
                />
              </label>
              <label className={label}>
                Supersedes (optional)
                <Select
                  className={input}
                  value={product.supersedesId}
                  onChange={(e) => setProduct({ ...product, supersedesId: e.target.value })}
                >
                  <option value="">New product</option>
                  {(productsQuery.data ?? [])
                    .filter((item) => item.status === "approved")
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.code} · {item.name}
                      </option>
                    ))}
                </Select>
              </label>
              <label className={label}>
                Minimum amount (₦)
                <input
                  className={input}
                  inputMode="decimal"
                  required
                  value={product.minAmount}
                  onChange={(e) => setProduct({ ...product, minAmount: e.target.value })}
                />
              </label>
              <label className={label}>
                Maximum amount (₦)
                <input
                  className={input}
                  inputMode="decimal"
                  required
                  value={product.maxAmount}
                  onChange={(e) => setProduct({ ...product, maxAmount: e.target.value })}
                />
              </label>
              <label className={label}>
                Minimum term (months)
                <input
                  className={input}
                  type="number"
                  min="1"
                  required
                  value={product.minTerm}
                  onChange={(e) => setProduct({ ...product, minTerm: e.target.value })}
                />
              </label>
              <label className={label}>
                Maximum term (months)
                <input
                  className={input}
                  type="number"
                  min="1"
                  required
                  value={product.maxTerm}
                  onChange={(e) => setProduct({ ...product, maxTerm: e.target.value })}
                />
              </label>
              <label className={label}>
                Monthly interest rate (%)
                <input
                  className={input}
                  inputMode="decimal"
                  required
                  value={product.monthlyRate}
                  onChange={(e) => {
                    const next = e.target.value;
                    setProduct({
                      ...product,
                      monthlyRate: next,
                      annualRate: (Number(next) * 12).toFixed(2),
                    });
                  }}
                />
                <span className="mt-1 block text-xs font-normal text-muted">
                  {product.annualRate}% a year ({product.monthlyRate || "0.00"}% a month)
                </span>
              </label>
              <label className={label}>
                Interest method
                <Select
                  className={input}
                  value={product.interestMethod}
                  onChange={(e) => setProduct({ ...product, interestMethod: e.target.value })}
                >
                  <option value="reducing_balance">Reducing balance</option>
                  <option value="flat">Flat</option>
                </Select>
              </label>
              <label className={label}>
                Fee (%)
                <input
                  className={input}
                  inputMode="decimal"
                  required
                  value={product.feePercent}
                  onChange={(e) => setProduct({ ...product, feePercent: e.target.value })}
                />
              </label>
              <label className={label}>
                Guarantors required
                <input
                  className={input}
                  type="number"
                  min="0"
                  required
                  value={product.guarantors}
                  onChange={(e) => setProduct({ ...product, guarantors: e.target.value })}
                />
              </label>
              <label className={label}>
                Effective from (WAT)
                <input
                  className={input}
                  type="date"
                  required
                  value={product.effectiveOn}
                  onChange={(e) => setProduct({ ...product, effectiveOn: e.target.value })}
                />
              </label>
              <label className={label}>
                Reason
                <input
                  className={input}
                  required
                  maxLength={1000}
                  value={product.reason}
                  onChange={(e) => setProduct({ ...product, reason: e.target.value })}
                />
              </label>
              {proposeProduct.isError && (
                <p role="alert" className="text-sm text-danger sm:col-span-2 xl:col-span-3">
                  {errorText(proposeProduct.error)}
                </p>
              )}
              {proposeProduct.isSuccess && (
                <p role="status" className="text-sm text-success sm:col-span-2 xl:col-span-3">
                  Product proposal submitted; it is waiting for another admin.
                </p>
              )}
              <div className="sm:col-span-2 xl:col-span-3">
                <Button type="submit" loading={proposeProduct.isPending}>
                  Submit product proposal
                </Button>
              </div>
            </form>
          </section>
          <section className={card}>
            <h2 className="font-serif text-xl font-semibold">Loan products and versions</h2>
            {approveProduct.isError && (
              <p role="alert" className="mt-3 text-sm text-danger">
                {errorText(approveProduct.error)}
              </p>
            )}
            {productsQuery.isPending && <Skeleton className="mt-4 h-32" />}
            {productsQuery.isError && (
              <p role="alert" className="mt-4 text-sm text-danger">
                {errorText(productsQuery.error)}
              </p>
            )}
            {!productsQuery.isPending && groupedProducts.size === 0 && (
              <EmptyState
                title="No loan products"
                detail="Proposed and approved product versions will appear here."
              />
            )}
            {[...groupedProducts.entries()].map(([code, versions]) => (
              <div key={code} className="mt-4 border-t border-border pt-4">
                <h3 className="font-semibold">
                  {versions[0].name} <span className="text-xs text-muted">{code}</span>
                </h3>
                {versions.map((item) => (
                  <article key={item.id} className="mt-3 rounded-control bg-[#F7F5EF] p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>{item.status}</Badge>
                      <span>
                        {item.annual_rate_bps / 100}% yearly · {item.interest_method}
                      </span>
                    </div>
                    <p className="mt-1">
                      ₦{(item.min_amount_kobo / 100).toLocaleString()}–₦
                      {(item.max_amount_kobo / 100).toLocaleString()} · {item.min_tenor_months}–
                      {item.max_tenor_months} months · {item.guarantors_required} guarantors
                    </p>
                    <p className="mt-1 text-xs text-muted">
                      Proposed by{" "}
                      {actorLabel(item.proposed_by_name, item.proposed_by_email, isAdmin)} ·
                      Approved by{" "}
                      {actorLabel(item.approved_by_name, item.approved_by_email, isAdmin)}
                    </p>
                    {item.approved_at && (
                      <p className="mt-1 text-xs text-muted">
                        Approved {displayWAT(item.approved_at)}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            ))}
          </section>
        </>
      )}
    </>
  );
}

function PolicyHistoryItem({
  item,
  isAdmin,
  pending,
  onAction,
}: {
  item: SettingsPolicyVersion;
  isAdmin: boolean;
  pending: boolean;
  onAction: (action: "approve" | "supersede", reason: string, end?: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [end, setEnd] = useState("");
  return (
    <article className="mt-3 rounded-control border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{item.status}</Badge>
        <strong>Version {item.version}</strong>
        <span>
          {item.effective_from} to {item.effective_to ?? "open"} (WAT)
        </span>
      </div>
      <p className="mt-1 text-xs text-muted">
        Proposed by {actorLabel(item.proposed_by_name, item.proposed_by_email, isAdmin)}
        {item.created_at ? ` on ${displayWAT(item.created_at)}` : ""} · Approved by{" "}
        {actorLabel(item.approved_by_name, item.approved_by_email, isAdmin)}
      </p>
      {item.status === "approved" && isAdmin && (
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            aria-label="Supersede effective-to date"
            className={`${input} max-w-[190px]`}
            type="date"
            required
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
          <input
            aria-label="Supersede reason"
            className={`${input} max-w-sm`}
            placeholder="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            variant="secondary"
            disabled={!end || !reason.trim() || pending}
            onClick={() => {
              if (window.confirm("Supersede this policy version?"))
                onAction("supersede", reason.trim(), end);
            }}
          >
            Supersede
          </Button>
        </div>
      )}
    </article>
  );
}

function SettingsApprovalTab({
  policies,
  products,
  effective,
  userId,
  isAdmin,
  pending,
  onApprovePolicy,
  onRejectPolicy,
  onApproveProduct,
  onRejectProduct,
}: {
  policies: SettingsPolicyVersion[];
  products: SettingsLoanProduct[];
  effective?: EffectivePolicy;
  userId: string;
  isAdmin: boolean;
  pending: boolean;
  onApprovePolicy: (id: string, reason: string) => void;
  onRejectPolicy: (id: string, reason: string) => void;
  onApproveProduct: (id: string, reason: string) => void;
  onRejectProduct: (id: string, reason: string) => void;
}) {
  const pendingPolicies = policies.filter((item) => item.status === "proposed");
  const pendingProducts = products.filter((item) => item.status === "proposed");
  if (!pendingPolicies.length && !pendingProducts.length)
    return (
      <EmptyState
        title="No settings changes need approval"
        detail="Pending policy and loan-product proposals will appear here."
      />
    );
  const proposedPolicy = pendingPolicies[0];
  return (
    <section className={card} aria-label="Settings proposals needing approval">
      <h2 className="font-serif text-xl font-semibold">Needs approval</h2>
      {proposedPolicy && (
        <ReviewPanel
          title={`Policy version ${proposedPolicy.version}`}
          rows={buildPolicyReviewRows(
            policies
              .filter(
                (item) =>
                  item.status === "approved" &&
                  item.effective_from <= proposedPolicy.effective_from,
              )
              .sort((left, right) => right.effective_from.localeCompare(left.effective_from))[0] ??
              effective ??
              {},
            proposedPolicy,
          )}
          effectiveFrom={proposedPolicy.effective_from}
          reason="Policy change"
          proposer={actorLabel(
            proposedPolicy.proposed_by_name,
            proposedPolicy.proposed_by_email,
            isAdmin,
          )}
          proposedAt={proposedPolicy.created_at}
          isProposer={proposedPolicy.proposed_by === userId}
          canApprove={canApproveSettings(
            { roles: isAdmin ? ["admin"] : [], userId },
            proposedPolicy.proposed_by ?? "",
          )}
          pending={pending}
          onApprove={(reason) => onApprovePolicy(proposedPolicy.id, reason)}
          onReject={(reason) => onRejectPolicy(proposedPolicy.id, reason)}
        />
      )}
      {pendingProducts.map((product) => {
        const superseded = products.find((item) => item.id === product.supersedes_id);
        return (
          <ReviewPanel
            key={product.id}
            title={`Loan product: ${product.name}`}
            comparisonLabel="Changes from the version this product supersedes"
            rows={buildProductReviewRows(superseded ?? {}, product)}
            effectiveFrom={product.effective_from}
            reason="Loan product change"
            proposer={actorLabel(product.proposed_by_name, product.proposed_by_email, isAdmin)}
            proposedAt={product.created_at}
            isProposer={product.proposed_by === userId}
            canApprove={canApproveSettings(
              { roles: isAdmin ? ["admin"] : [], userId },
              product.proposed_by ?? "",
            )}
            pending={pending}
            onApprove={(reason) => onApproveProduct(product.id, reason)}
            onReject={(reason) => onRejectProduct(product.id, reason)}
          />
        );
      })}
    </section>
  );
}

function ReviewPanel({
  title,
  comparisonLabel = "Changes from the currently effective policy",
  rows,
  effectiveFrom,
  reason,
  proposer,
  proposedAt,
  isProposer,
  canApprove,
  pending,
  onApprove,
  onReject,
}: {
  title: string;
  comparisonLabel?: string;
  rows: { key: string; label: string; current: string; proposed: string; changed: boolean }[];
  effectiveFrom?: string | null;
  reason: string;
  proposer: string;
  proposedAt?: string;
  isProposer: boolean;
  canApprove: boolean;
  pending: boolean;
  onApprove: (reason: string) => void;
  onReject: (reason: string) => void;
}) {
  const [decisionReason, setDecisionReason] = useState("");
  const summary = rows.map((row) => `${row.label}: ${row.current} → ${row.proposed}`).join("; ");
  return (
    <article className="mt-4 rounded-control border-2 border-amber-300 bg-amber-50/50 p-4">
      <h3 className="font-semibold">{title}</h3>
      <p className="mt-1 text-sm">{comparisonLabel}</p>
      <table className="mt-3 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="p-2">Label</th>
            <th className="p-2">Current value</th>
            <th className="p-2">Proposed value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className={row.changed ? "bg-amber-100" : ""}>
              <td className="p-2">{row.label}</td>
              <td className="p-2">{row.current}</td>
              <td className="p-2 font-medium">{row.proposed}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-sm">
        <strong>Effective from:</strong> {effectiveFrom ? `${effectiveFrom} (WAT)` : "—"}
      </p>
      <p className="text-sm">
        <strong>Reason:</strong> {reason}
      </p>
      <p className="text-sm">
        <strong>Proposed by:</strong> {proposer}
        {proposedAt ? ` · ${displayWAT(proposedAt)}` : ""}
      </p>
      {isProposer ? (
        <p className="mt-3 text-sm text-amber-900">You proposed this; another admin must approve</p>
      ) : canApprove ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            className={`${input} max-w-sm`}
            aria-label="Decision reason"
            value={decisionReason}
            onChange={(event) => setDecisionReason(event.target.value)}
            placeholder="Decision reason"
          />
          <Button
            disabled={pending || !decisionReason.trim()}
            onClick={() => {
              if (
                window.confirm(
                  `Approve this settings change? ${summary}. Effective from ${effectiveFrom} (WAT).`,
                )
              )
                onApprove(decisionReason.trim());
            }}
          >
            Approve
          </Button>
          <Button
            variant="secondary"
            disabled={pending}
            onClick={() => {
              const rejection = window.prompt("Reason for rejecting this settings change?");
              if (rejection?.trim()) onReject(rejection.trim());
            }}
          >
            Reject
          </Button>
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted">
          Only another admin can approve or reject this change.
        </p>
      )}
    </article>
  );
}
