import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import {
  ApiError,
  getCashAwaitingDeposit,
  getCashOnHandSummary,
  recordCashDeposit,
} from "../api/client";
import type { CashReceipt } from "../api/schema";
import { completeAttempt, createAttemptKeyState, getAttemptKey } from "../memberActionForm.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateReceived, Money } from "../ui/Format";
import { Input } from "../ui/Input";

function message(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "The cash records could not be loaded. Please try again.";
}

function watToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Lagos" }).format(new Date());
}

export function PaymentCashPanel({ roles, userId }: { roles: string[]; userId: string }) {
  const canDeposit = roles.includes("treasurer");
  const canViewSummary = canDeposit || roles.includes("admin") || roles.includes("auditor");
  const queryClient = useQueryClient();
  const keys = useRef(createAttemptKeyState());
  const actionLock = useRef(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [slipReference, setSlipReference] = useState("");
  const [depositedOn, setDepositedOn] = useState(watToday);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const receipts = useQuery({
    queryKey: ["cash-awaiting-deposit"],
    queryFn: getCashAwaitingDeposit,
    retry: false,
  });
  const summary = useQuery({
    queryKey: ["cash-on-hand-summary"],
    queryFn: getCashOnHandSummary,
    enabled: canViewSummary,
    retry: false,
  });

  const resetAttempt = () => completeAttempt(keys.current, "cash-deposit");
  const toggleReceipt = (receipt: CashReceipt) => {
    if (!canDeposit || receipt.recorded_by === userId) return;
    setSelectedIds((ids) =>
      ids.includes(receipt.id) ? ids.filter((id) => id !== receipt.id) : [...ids, receipt.id],
    );
    resetAttempt();
    setError("");
    setSuccess("");
  };

  const confirmDeposit = async () => {
    if (!canDeposit || selectedIds.length === 0 || !slipReference.trim()) {
      setError("Choose eligible receipts and enter the bank slip reference.");
      return;
    }
    if (busy || actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    const idempotencyKey = getAttemptKey(keys.current, "cash-deposit");
    try {
      const result = await recordCashDeposit(
        {
          slip_reference: slipReference.trim(),
          deposited_on: depositedOn,
          payment_ids: selectedIds,
        },
        idempotencyKey,
      );
      completeAttempt(keys.current, "cash-deposit");
      setConfirmOpen(false);
      setSelectedIds([]);
      setSlipReference("");
      setDepositedOn(watToday());
      setSuccess(`Deposit recorded. Slip ${result.slip_reference}.`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["cash-awaiting-deposit"] }),
        queryClient.invalidateQueries({ queryKey: ["cash-on-hand-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["payments"] }),
        queryClient.invalidateQueries({ queryKey: ["notification-summary"] }),
      ]);
    } catch (failure) {
      setError(message(failure));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  };

  return (
    <section className="space-y-5" aria-label="Cash awaiting deposit">
      {canViewSummary && (
        <section
          className="rounded-card border border-border bg-surface p-4"
          aria-label="Cash on hand by officer"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="font-serif text-lg font-semibold">Cash on hand</h2>
            {summary.data && (
              <p className="text-sm font-semibold">
                Ledger total: <Money kobo={summary.data.ledger_balance_kobo} />
              </p>
            )}
          </div>
          {summary.isPending && <Skeleton className="mt-3 h-16 w-full" />}
          {summary.isError && (
            <div className="mt-3 flex items-center gap-2 text-sm text-danger" role="alert">
              <span>{message(summary.error)}</span>
              <Button type="button" variant="secondary" onClick={() => void summary.refetch()}>
                Retry
              </Button>
            </div>
          )}
          {summary.data && (
            <>
              {summary.data.unallocated_kobo !== 0 && (
                <p className="mt-2 text-sm text-danger" role="alert">
                  Officer receipts differ from the cash-on-hand ledger by{" "}
                  <Money kobo={summary.data.unallocated_kobo} />.
                </p>
              )}
              {summary.data.officers.length === 0 ? (
                <p className="mt-3 text-sm text-muted">
                  No cash is currently assigned to an officer.
                </p>
              ) : (
                <ul className="mt-3 divide-y divide-border">
                  {summary.data.officers.map((officer) => (
                    <li
                      key={officer.user_id}
                      className="flex flex-wrap justify-between gap-2 py-2 text-sm"
                    >
                      <span>
                        {officer.display_name} · oldest {officer.oldest_age_days}{" "}
                        {officer.oldest_age_days === 1 ? "day" : "days"}
                      </span>
                      <span className="font-medium">
                        <Money kobo={officer.amount_kobo} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {success && (
        <p className="text-sm text-green-700" role="status">
          {success}
        </p>
      )}
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      {receipts.isPending && <Skeleton className="h-36 w-full" />}
      {receipts.isError && (
        <EmptyState
          title="Cash receipts could not be loaded"
          detail={message(receipts.error)}
          action={<Button onClick={() => void receipts.refetch()}>Try again</Button>}
        />
      )}
      {receipts.data && receipts.data.length === 0 && (
        <EmptyState
          title="No cash awaiting deposit"
          detail="Recorded cash receipts will appear here until they are banked."
        />
      )}
      {receipts.data && receipts.data.length > 0 && (
        <div className="overflow-x-auto rounded-card border border-border bg-surface">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-[#FAF9F6] text-xs uppercase text-muted">
              <tr>
                {canDeposit && <th className="p-3">Select</th>}
                <th className="p-3">Receipt</th>
                <th className="p-3">Member</th>
                <th className="p-3">Amount</th>
                <th className="p-3">Received</th>
                <th className="p-3">Recorded by</th>
                <th className="p-3">Age / window</th>
              </tr>
            </thead>
            <tbody>
              {receipts.data.map((receipt) => {
                const selfRecorded = receipt.recorded_by === userId;
                const isSelected = selectedIds.includes(receipt.id);
                return (
                  <tr key={receipt.id} className="border-b border-border align-top">
                    {canDeposit && (
                      <td className="p-3">
                        <input
                          type="checkbox"
                          aria-label={`Select receipt ${receipt.receipt_no ?? receipt.id}`}
                          checked={isSelected}
                          disabled={selfRecorded}
                          onChange={() => toggleReceipt(receipt)}
                        />
                        {selfRecorded && (
                          <p className="mt-1 max-w-40 text-xs text-muted">
                            You recorded this; another treasurer must confirm.
                          </p>
                        )}
                      </td>
                    )}
                    <td className="p-3">
                      {receipt.receipt_no ? (
                        <a
                          className="text-primary underline"
                          href={`/payments/${receipt.id}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {receipt.receipt_no}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="p-3">
                      <span>{receipt.member_name}</span>
                      <span className="block text-xs text-muted">{receipt.member_no}</span>
                    </td>
                    <td className="p-3">
                      <Money kobo={receipt.amount_kobo} />
                    </td>
                    <td className="p-3">
                      <DateReceived value={receipt.received_on} />
                    </td>
                    <td className="p-3">{receipt.recorded_by_name}</td>
                    <td className="p-3">
                      <span>
                        {receipt.age_days} {receipt.age_days === 1 ? "day" : "days"} /{" "}
                        {receipt.cash_banking_days}
                      </span>
                      {receipt.overdue && <Badge status="overdue" />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {canDeposit && receipts.data && receipts.data.length > 0 && (
        <section className="max-w-2xl rounded-card border border-border bg-surface p-4">
          <h3 className="font-semibold">Bank cash deposit</h3>
          <p className="mt-1 text-sm text-muted">
            Select only receipts you did not record. The deposit is posted to the bank ledger.
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-medium">
              Bank slip reference
              <Input
                value={slipReference}
                maxLength={160}
                onChange={(event) => {
                  setSlipReference(event.target.value);
                  resetAttempt();
                  setError("");
                }}
              />
            </label>
            <label className="block text-sm font-medium">
              Deposit date
              <Input
                type="date"
                value={depositedOn}
                onChange={(event) => {
                  setDepositedOn(event.target.value);
                  resetAttempt();
                  setError("");
                }}
              />
            </label>
          </div>
          <Button
            className="mt-4"
            type="button"
            disabled={busy || selectedIds.length === 0 || !slipReference.trim()}
            onClick={() => {
              setError("");
              setConfirmOpen(true);
            }}
          >
            Review deposit ({selectedIds.length} receipts)
          </Button>
          <Dialog.Root open={confirmOpen} onOpenChange={(open) => !busy && setConfirmOpen(open)}>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-50 bg-[#0E2F2B]/55" />
              <Dialog.Content className="fixed left-1/2 top-1/2 z-[51] w-[min(92vw,480px)] -translate-x-1/2 -translate-y-1/2 rounded-card border border-border bg-surface p-5 shadow-xl">
                <Dialog.Title className="font-serif text-xl font-semibold">
                  Confirm cash deposit
                </Dialog.Title>
                <Dialog.Description className="mt-3 text-sm text-muted">
                  Bank {selectedIds.length} selected cash receipt
                  {selectedIds.length === 1 ? "" : "s"} under slip {slipReference.trim()} on{" "}
                  <DateReceived value={depositedOn} />?
                </Dialog.Description>
                {error && (
                  <p className="mt-3 text-sm text-danger" role="alert">
                    {error}
                  </p>
                )}
                <div className="mt-5 flex justify-end gap-2">
                  <Dialog.Close asChild>
                    <Button type="button" variant="secondary" disabled={busy}>
                      Back
                    </Button>
                  </Dialog.Close>
                  <Button
                    type="button"
                    loading={busy}
                    disabled={busy}
                    onClick={() => void confirmDeposit()}
                  >
                    Confirm and post
                  </Button>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </section>
      )}
    </section>
  );
}
