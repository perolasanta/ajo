import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import {
  ApiError,
  authorizeDisbursement,
  confirmDisbursementPaid,
  failDisbursement,
  getDisbursement,
  recordDisbursementTransfer,
  request,
  reverseDisbursement,
} from "../api/client";
import type { DisbursementRecord } from "../api/schema";
import { eventsForDisbursement } from "../disbursementTimeline.js";
import {
  canAuthorizeDisbursement,
  canConfirmDisbursementPaid,
  canFailDisbursement,
  canRecordDisbursementTransfer,
  canReverseDisbursement,
  shouldExplainPreparedByCurrentUser,
} from "../disbursementPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Timeline } from "../ui/Timeline";

function DetailError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  return (
    <EmptyState
      title="Disbursement could not be loaded"
      detail={message}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}
function DetailLoading() {
  return (
    <div className="space-y-4" aria-label="Loading disbursement">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-44 w-full" />
      <Skeleton className="h-56 w-full" />
    </div>
  );
}
function Summary({ item }: { item: DisbursementRecord }) {
  return (
    <section className="rounded-card border border-border bg-surface p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted">{item.member_no}</p>
          <h2 className="mt-1 font-serif text-2xl font-semibold">{item.full_name}</h2>
        </div>
        <Badge status={item.status} />
      </div>
      <dl className="mt-5 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted">Amount</dt>
          <dd className="mt-1 font-semibold">
            <Money kobo={item.amount_kobo} />
          </dd>
        </div>
        <div>
          <dt className="text-muted">Prepared</dt>
          <dd className="mt-1">
            <DateTime value={item.created_at} />
          </dd>
        </div>
        <div>
          <dt className="text-muted">Loan ID</dt>
          <dd className="mt-1 break-all">{item.loan_id}</dd>
        </div>
        <div>
          <dt className="text-muted">Application ID</dt>
          <dd className="mt-1 break-all">{item.application_id}</dd>
        </div>
        {"bank_code" in item && (
          <div>
            <dt className="text-muted">Destination bank</dt>
            <dd className="mt-1">
              {item.bank_code} ···· {item.last4}
            </dd>
          </div>
        )}
        {item.transfer_reference && (
          <div>
            <dt className="text-muted">Bank transfer reference</dt>
            <dd className="mt-1 break-all">{item.transfer_reference}</dd>
          </div>
        )}
        {item.transfer_date && (
          <div>
            <dt className="text-muted">Transfer date</dt>
            <dd className="mt-1">{item.transfer_date}</dd>
          </div>
        )}
        {item.failure_reason && (
          <div className="sm:col-span-2">
            <dt className="text-muted">Failure reason</dt>
            <dd className="mt-1">{item.failure_reason}</dd>
          </div>
        )}
        {item.reversal_reason && (
          <div className="sm:col-span-2">
            <dt className="text-muted">Reversal reason</dt>
            <dd className="mt-1">{item.reversal_reason}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
type ActionInput =
  | { type: "authorize" }
  | { type: "transfer"; reference: string; transfer_date: string }
  | { type: "confirm" }
  | { type: "fail"; reason: string }
  | { type: "reverse"; reason: string };
function Actions({
  item,
  roles,
  userId,
}: {
  item: DisbursementRecord;
  roles: string[];
  userId: string;
}) {
  const queryClient = useQueryClient();
  const [reference, setReference] = useState("");
  const [transferDate, setTransferDate] = useState(new Date().toISOString().slice(0, 10));
  const [failureReason, setFailureReason] = useState("");
  const [reversalReason, setReversalReason] = useState("");
  const action = useMutation({
    mutationFn: async (input: ActionInput) => {
      if (input.type === "authorize") await authorizeDisbursement(item.id);
      else if (input.type === "transfer")
        await recordDisbursementTransfer(item.id, {
          reference: input.reference,
          transfer_date: input.transfer_date,
        });
      else if (input.type === "confirm") await confirmDisbursementPaid(item.id);
      else if (input.type === "fail") await failDisbursement(item.id, { reason: input.reason });
      else await reverseDisbursement(item.id, { reason: input.reason });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["disbursement", item.id] });
      await queryClient.invalidateQueries({ queryKey: ["disbursements"] });
      await queryClient.invalidateQueries({ queryKey: ["ready-disbursement-loans"] });
      setFailureReason("");
      setReversalReason("");
    },
  });
  const confirmAction = (input: ActionInput, text: string) => {
    if (window.confirm(text)) action.mutate(input);
  };
  const canAuthorize = canAuthorizeDisbursement(roles, userId, item);
  const explainMakerCheck =
    roles.includes("admin") && shouldExplainPreparedByCurrentUser(userId, item);
  const canTransfer = canRecordDisbursementTransfer(roles, userId, item);
  const canConfirm = canConfirmDisbursementPaid(roles, userId, item);
  const canFail = canFailDisbursement(roles, userId, item);
  const canReverse = canReverseDisbursement(roles, userId, item);
  const hasAction =
    canAuthorize || explainMakerCheck || canTransfer || canConfirm || canFail || canReverse;
  const errorMessage =
    action.error instanceof ApiError
      ? action.error.message
      : action.isError
        ? "Action failed. Please try again."
        : null;
  if (!hasAction) return null;
  return (
    <section className="rounded-card border border-border bg-surface p-4 sm:p-6">
      <h2 className="font-serif text-xl font-semibold">Available actions</h2>
      <p className="mt-1 text-sm text-muted">
        Ajo records the workflow only; the transfer is made manually outside the app.
      </p>
      {explainMakerCheck && (
        <p className="mt-3 rounded-control bg-warning-soft p-3 text-sm text-warning">
          You prepared this disbursement, so you cannot authorize it.
        </p>
      )}
      {errorMessage && (
        <p role="alert" className="mt-3 rounded-control bg-danger-soft p-3 text-sm text-danger">
          {errorMessage}
        </p>
      )}
      {canAuthorize && (
        <Button
          className="mt-4"
          disabled={action.isPending}
          onClick={() =>
            confirmAction(
              { type: "authorize" },
              "Authorize this disbursement? The bank transfer still needs to be made manually.",
            )
          }
        >
          {action.isPending ? "Submitting…" : "Authorize"}
        </Button>
      )}
      {canTransfer && (
        <form
          className="mt-4 grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            confirmAction(
              { type: "transfer", reference, transfer_date: transferDate },
              "Record that this manual bank transfer was made?",
            );
          }}
        >
          <Field label="Bank transfer reference">
            <Input
              required
              maxLength={200}
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </Field>
          <Field label="Transfer date">
            <Input
              required
              type="date"
              value={transferDate}
              onChange={(event) => setTransferDate(event.target.value)}
            />
          </Field>
          <Button
            className="sm:col-span-2"
            disabled={action.isPending || !reference.trim() || !transferDate}
          >
            {action.isPending ? "Submitting…" : "Record transfer"}
          </Button>
        </form>
      )}
      {canConfirm && (
        <Button
          className="mt-4"
          disabled={action.isPending}
          onClick={() =>
            confirmAction(
              { type: "confirm" },
              "Confirm this manually transferred disbursement as paid and post it to the ledger?",
            )
          }
        >
          {action.isPending ? "Submitting…" : "Confirm paid"}
        </Button>
      )}
      {canFail && (
        <form
          className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            confirmAction(
              { type: "fail", reason: failureReason.trim() },
              "Record this transfer as failed?",
            );
          }}
        >
          <Field label="Failure reason">
            <Input
              required
              maxLength={1000}
              value={failureReason}
              onChange={(event) => setFailureReason(event.target.value)}
            />
          </Field>
          <Button variant="secondary" disabled={action.isPending || !failureReason.trim()}>
            {action.isPending ? "Submitting…" : "Record failure"}
          </Button>
        </form>
      )}
      {canReverse && (
        <form
          className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            confirmAction(
              { type: "reverse", reason: reversalReason.trim() },
              "Reverse this paid disbursement? This creates a reversing ledger entry.",
            );
          }}
        >
          <Field label="Reversal reason">
            <Input
              required
              maxLength={1000}
              value={reversalReason}
              onChange={(event) => setReversalReason(event.target.value)}
            />
          </Field>
          <Button variant="secondary" disabled={action.isPending || !reversalReason.trim()}>
            {action.isPending ? "Submitting…" : "Reverse paid disbursement"}
          </Button>
        </form>
      )}
    </section>
  );
}
export default function DisbursementDetailPage() {
  const { disbursementId = "" } = useParams();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<{ user_id: string; roles: string[] }>("/auth/me"),
  });
  const detail = useQuery({
    queryKey: ["disbursement", disbursementId],
    queryFn: () => getDisbursement(disbursementId),
    enabled: Boolean(disbursementId),
  });
  return (
    <>
      <PageHeader title="Disbursement detail" breadcrumb="Manual bank transfer" />
      <Link
        to="/disbursements"
        className="mb-5 inline-flex min-h-11 items-center text-sm text-primary hover:underline"
      >
        ← Back to disbursements
      </Link>
      <p className="mb-5 max-w-2xl text-sm text-muted">
        The bank transfer happens outside Ajo. This page records its manual workflow and
        confirmation.
      </p>
      {detail.isPending && <DetailLoading />}
      {detail.isError && <DetailError error={detail.error} retry={() => void detail.refetch()} />}
      {detail.data && (
        <div className="space-y-6">
          <Summary item={detail.data} />
          <section className="rounded-card border border-border bg-surface p-4 sm:p-6">
            <h2 className="mb-5 font-serif text-xl font-semibold">Workflow history</h2>
            <Timeline events={eventsForDisbursement(detail.data)} />
          </section>
          {session.data && (
            <Actions item={detail.data} roles={session.data.roles} userId={session.data.user_id} />
          )}
        </div>
      )}
    </>
  );
}
