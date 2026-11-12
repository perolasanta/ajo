import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { PaymentListItem } from "../api/schema";
import { ApiError, confirmPayment, rejectPayment } from "../api/client";
import { completeAttempt, createAttemptKeyState, getAttemptKey } from "../memberActionForm.mjs";
import { Button } from "../ui/Button";

type ActionKind = "confirm" | "reject";

function requestError(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "The payment action failed. Please try again.";
}

export function PaymentDecisionActions({
  payment,
  userId,
  roles,
}: {
  payment: PaymentListItem;
  userId: string;
  roles: string[];
}) {
  const canDecide = roles.includes("officer") || roles.includes("treasurer");
  const isRecorder = payment.recorded_by === userId;
  const [action, setAction] = useState<ActionKind | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const actionLock = useRef(false);
  const attemptKeys = useRef(createAttemptKeyState());
  const queryClient = useQueryClient();

  if (!canDecide || payment.channel !== "bank_transfer" || payment.status !== "pending") {
    return null;
  }

  const openAction = (nextAction: ActionKind) => {
    setAction(nextAction);
    setReason("");
    setError("");
  };

  const submit = async () => {
    if (!action || busy || actionLock.current) return;
    if (action === "reject" && !reason.trim()) {
      setError("Enter a reason for rejecting this transfer.");
      return;
    }
    actionLock.current = true;
    setBusy(true);
    setError("");
    const formName = `${payment.id}:${action}`;
    const idempotencyKey = getAttemptKey(attemptKeys.current, formName);
    try {
      const body = action === "reject" ? { reason: reason.trim() } : {};
      if (action === "confirm") {
        await confirmPayment(payment.id, body, idempotencyKey);
      } else {
        await rejectPayment(payment.id, body, idempotencyKey);
      }
      completeAttempt(attemptKeys.current, formName);
      setAction(null);
      setReason("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["payments"] }),
        queryClient.invalidateQueries({ queryKey: ["notification-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["payment", payment.id] }),
      ]);
    } catch (requestFailure) {
      setError(requestError(requestFailure));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  };

  if (isRecorder) {
    return (
      <div className="space-y-1">
        <Button type="button" variant="secondary" disabled>
          Confirm
        </Button>
        <p className="max-w-48 text-xs text-muted">
          You recorded this; another person must confirm.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" onClick={() => openAction("confirm")}>
        Confirm
      </Button>
      <Button type="button" size="sm" variant="secondary" onClick={() => openAction("reject")}>
        Reject
      </Button>
      <Dialog.Root
        open={action !== null}
        onOpenChange={(open) => {
          if (!busy && !open) {
            setAction(null);
            setError("");
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-[#0E2F2B]/55" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[51] w-[min(92vw,480px)] -translate-x-1/2 -translate-y-1/2 rounded-card border border-border bg-surface p-5 shadow-xl">
            <Dialog.Title className="font-serif text-xl font-semibold">
              {action === "reject" ? "Reject bank transfer" : "Confirm bank transfer"}
            </Dialog.Title>
            <Dialog.Description className="mt-3 text-sm text-muted">
              {action === "reject"
                ? `Reject this transfer recorded by ${payment.recorded_by_name}? Your reason will be shown to the recorder.`
                : `Confirm this transfer recorded by ${payment.recorded_by_name}?`}
            </Dialog.Description>
            {action === "reject" && (
              <label className="mt-4 block text-sm font-medium">
                Reason
                <textarea
                  className="mt-1 min-h-24 w-full rounded-card border border-border bg-surface px-3 py-2"
                  value={reason}
                  maxLength={500}
                  required
                  onChange={(event) => {
                    setReason(event.target.value);
                    completeAttempt(attemptKeys.current, `${payment.id}:reject`);
                    setError("");
                  }}
                />
              </label>
            )}
            {error && (
              <p className="mt-3 text-sm text-danger" role="alert">
                {error}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <Dialog.Close asChild>
                <Button type="button" variant="secondary" disabled={busy}>
                  Cancel
                </Button>
              </Dialog.Close>
              <Button type="button" loading={busy} disabled={busy} onClick={() => void submit()}>
                {action === "reject" ? "Confirm rejection" : "Confirm transfer"}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
