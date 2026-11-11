import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { ApiError, getMemberLoans, getMembers, recordPayment } from "../api/client";
import type { MemberLoanItem } from "../api/schema";
import {
  buildPaymentPayload,
  canRecordPayments,
  formatKoboAsNaira,
  nairaToKobo,
  validatePaymentDraft,
  type PaymentFormDraft,
  type PaymentPurpose,
} from "../paymentForm.mjs";
import { completeAttempt, createAttemptKeyState, getAttemptKey } from "../memberActionForm.mjs";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { Select } from "../ui/Select";

type FormFields = Omit<PaymentFormDraft, "roles" | "loanStatus" | "deductionsPaused">;

function todayInWAT(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Lagos" }).format(new Date());
}

function emptyForm(): FormFields {
  return {
    memberId: "",
    channel: "cash",
    purpose: "savings",
    amountNaira: "",
    providerReference: "",
    receivedOn: todayInWAT(),
    loanId: "",
    reason: "",
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "The payment could not be recorded. Please try again.";
}

function hasManualException(loan: MemberLoanItem | undefined): boolean {
  return loan?.loan_status === "disputed" || Boolean(loan?.deductions_paused);
}

export function PaymentRecordForm({ roles }: { roles: string[] }) {
  const canRecord = canRecordPayments(roles);
  const queryClient = useQueryClient();
  const attemptKeys = useRef(createAttemptKeyState());
  const actionLock = useRef(false);
  const [draft, setDraft] = useState<FormFields>(emptyForm);
  const [memberSearch, setMemberSearch] = useState("");
  const [memberName, setMemberName] = useState("");
  const [formError, setFormError] = useState("");
  const [recordError, setRecordError] = useState("");
  const [success, setSuccess] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const members = useQuery({
    queryKey: ["payment-members", memberSearch],
    queryFn: () => getMembers(memberSearch.trim() || undefined),
    enabled: canRecord,
    retry: false,
  });
  const loans = useQuery({
    queryKey: ["payment-member-loans", draft.memberId],
    queryFn: () => getMemberLoans(draft.memberId, { page: 1, pageSize: 100 }),
    enabled: canRecord && Boolean(draft.memberId) && draft.purpose === "loan_repayment",
    retry: false,
  });
  const loanItems = loans.data?.items.filter((item) => Boolean(item.loan_id)) ?? [];
  const selectedLoan = loanItems.find((item) => item.loan_id === draft.loanId);
  const manualException = hasManualException(selectedLoan);
  const validationDraft: PaymentFormDraft = {
    ...draft,
    roles,
    loanStatus: selectedLoan?.loan_status ?? "",
    deductionsPaused: Boolean(selectedLoan?.deductions_paused),
  };
  let confirmationAmount = "—";
  try {
    confirmationAmount = formatKoboAsNaira(nairaToKobo(draft.amountNaira));
  } catch {
    // An invalid amount is reported by form validation before the dialog opens.
  }

  const updateDraft = <Key extends keyof FormFields>(key: Key, value: FormFields[Key]) => {
    setDraft((current) => ({ ...current, [key]: value }));
    completeAttempt(attemptKeys.current, "payment-record");
    setFormError("");
    setRecordError("");
    setSuccess("");
  };

  if (!canRecord) return null;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = validatePaymentDraft(validationDraft);
    if (message) {
      setFormError(message);
      return;
    }
    setFormError("");
    setRecordError("");
    setConfirmOpen(true);
  };

  const confirm = async () => {
    if (busy || actionLock.current) return;
    const message = validatePaymentDraft(validationDraft);
    if (message) {
      setFormError(message);
      setConfirmOpen(false);
      return;
    }
    actionLock.current = true;
    setBusy(true);
    setRecordError("");
    const idempotencyKey = getAttemptKey(attemptKeys.current, "payment-record");
    const successfulMemberName = memberName;
    try {
      const result = await recordPayment(buildPaymentPayload(validationDraft), idempotencyKey);
      completeAttempt(attemptKeys.current, "payment-record");
      setConfirmOpen(false);
      setDraft(emptyForm());
      setMemberSearch("");
      setMemberName("");
      setFormError("");
      setSuccess(
        result.receipt_no
          ? `Payment recorded for ${successfulMemberName}. Receipt no. ${result.receipt_no}.`
          : `Payment recorded for ${successfulMemberName}.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["payments"] });
      void queryClient.invalidateQueries({ queryKey: ["notification-summary"] });
    } catch (error) {
      // Keep the attempt key and the form values so a retry is idempotent.
      setRecordError(errorMessage(error));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  };

  return (
    <>
      <Card className="mb-6 max-w-3xl">
        <h2 className="font-serif text-xl font-semibold">Record a payment</h2>
        <p className="mt-1 text-sm text-muted">
          Record cash received or a bank transfer. Amounts are entered in naira.
        </p>
        {success && (
          <p
            className="mt-4 rounded-control bg-success-soft p-3 text-sm text-success"
            role="status"
          >
            {success}
          </p>
        )}
        {formError && (
          <p
            className="mt-4 rounded-control border border-danger/30 bg-danger/5 p-3 text-sm text-danger"
            role="alert"
          >
            {formError}
          </p>
        )}
        <form className="mt-4 space-y-4" onSubmit={submit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Find a member" helper="Search by member name or number.">
              <Input
                type="search"
                value={memberSearch}
                onChange={(event) => setMemberSearch(event.target.value)}
                placeholder="Search members"
              />
            </Field>
            <Field
              label="Member"
              error={members.isError ? "Members could not be loaded." : undefined}
              helper={members.isPending ? "Loading members…" : undefined}
            >
              <Select
                value={draft.memberId}
                disabled={members.isPending || members.isError}
                onChange={(event) => {
                  const id = event.target.value;
                  const member = members.data?.find((item) => item.id === id);
                  updateDraft("memberId", id);
                  updateDraft("loanId", "");
                  setMemberName(member?.full_name ?? "");
                }}
              >
                <option value="">Choose a member</option>
                {draft.memberId && !members.data?.some((item) => item.id === draft.memberId) && (
                  <option value={draft.memberId}>{memberName || "Selected member"}</option>
                )}
                {(members.data ?? []).map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.full_name} · {member.member_no}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {members.isError && (
            <div className="flex items-center gap-2 text-sm text-danger" role="alert">
              <span>{errorMessage(members.error)}</span>
              <Button type="button" variant="secondary" onClick={() => void members.refetch()}>
                Retry
              </Button>
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Channel">
              <Select
                value={draft.channel}
                onChange={(event) =>
                  updateDraft("channel", event.target.value as FormFields["channel"])
                }
              >
                <option value="cash">Cash</option>
                <option value="bank_transfer">Bank transfer</option>
              </Select>
            </Field>
            <Field label="Purpose">
              <Select
                value={draft.purpose}
                onChange={(event) => {
                  updateDraft("purpose", event.target.value as PaymentPurpose);
                  updateDraft("loanId", "");
                }}
              >
                <option value="savings">Savings</option>
                <option value="shares">Shares</option>
                <option value="dues">Dues</option>
                <option value="loan_repayment">Loan repayment</option>
              </Select>
            </Field>
          </div>
          {draft.purpose === "loan_repayment" && (
            <Field
              label="Loan"
              error={loans.isError ? "This member's loans could not be loaded." : undefined}
              helper={
                !draft.memberId
                  ? "Choose a member first."
                  : loans.isPending
                    ? "Loading this member's loans…"
                    : undefined
              }
            >
              <Select
                value={draft.loanId}
                disabled={
                  !draft.memberId || loans.isPending || loans.isError || loanItems.length === 0
                }
                onChange={(event) => updateDraft("loanId", event.target.value)}
              >
                <option value="">Choose a loan</option>
                {loanItems.map((loan) => {
                  const isManualException = hasManualException(loan);
                  const status = loan.loan_status ?? "loan";
                  const balance =
                    loan.outstanding_balance_kobo === undefined ||
                    loan.outstanding_balance_kobo === null
                      ? ""
                      : ` · balance ${formatKoboAsNaira(loan.outstanding_balance_kobo)}`;
                  const restriction =
                    isManualException && !roles.includes("treasurer") ? " · treasurer only" : "";
                  return (
                    <option
                      key={loan.loan_id}
                      value={loan.loan_id ?? ""}
                      disabled={isManualException && !roles.includes("treasurer")}
                    >
                      Loan {loan.loan_id?.slice(0, 8)} · {status}
                      {balance}
                      {restriction}
                    </option>
                  );
                })}
              </Select>
            </Field>
          )}
          {draft.purpose === "loan_repayment" && loans.data && loanItems.length === 0 && (
            <p className="text-sm text-muted" role="status">
              This member has no recorded loans to repay.
            </p>
          )}
          {draft.purpose === "loan_repayment" && loans.isError && (
            <Button type="button" variant="secondary" onClick={() => void loans.refetch()}>
              Retry loading loans
            </Button>
          )}
          {draft.purpose === "loan_repayment" && manualException && (
            <Field label="Reason for manual repayment">
              <textarea
                className="min-h-24 w-full rounded-card border border-border bg-surface px-3 py-2 text-base sm:text-sm"
                maxLength={500}
                required
                value={draft.reason}
                onChange={(event) => updateDraft("reason", event.target.value)}
              />
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Amount (₦)" helper="Use up to two decimal places.">
              <Input
                type="number"
                inputMode="decimal"
                min="0.01"
                step="0.01"
                value={draft.amountNaira}
                onChange={(event) => updateDraft("amountNaira", event.target.value)}
                required
              />
            </Field>
            <Field label="Date received">
              <Input
                type="date"
                value={draft.receivedOn}
                onChange={(event) => updateDraft("receivedOn", event.target.value)}
                required
              />
            </Field>
          </div>
          {draft.channel === "bank_transfer" && (
            <Field label="Bank reference">
              <Input
                value={draft.providerReference}
                onChange={(event) => updateDraft("providerReference", event.target.value)}
                maxLength={160}
                required
              />
            </Field>
          )}
          <Button type="submit" disabled={busy}>
            Review payment
          </Button>
        </form>
      </Card>

      <Dialog.Root open={confirmOpen} onOpenChange={(open) => !busy && setConfirmOpen(open)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-[#0E2F2B]/55" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[51] w-[min(92vw,480px)] -translate-x-1/2 -translate-y-1/2 rounded-card border border-border bg-surface p-5 shadow-xl">
            <Dialog.Title className="font-serif text-xl font-semibold">
              Confirm payment
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm text-muted">
              Record {confirmationAmount} for {memberName || "the selected member"}?
            </Dialog.Description>
            {recordError && (
              <p className="mt-3 text-sm text-danger" role="alert">
                {recordError}
              </p>
            )}
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Dialog.Close asChild>
                <Button type="button" variant="secondary" disabled={busy}>
                  Back to form
                </Button>
              </Dialog.Close>
              <Button type="button" loading={busy} disabled={busy} onClick={() => void confirm()}>
                Confirm and record
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
