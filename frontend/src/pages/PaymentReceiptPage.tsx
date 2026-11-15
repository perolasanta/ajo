import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError, getPayment, request } from "../api/client";
import type { PaymentListItem } from "../api/schema";
import { PaymentDecisionActions } from "./PaymentDecisionActions";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateReceived, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type Session = { user_id: string; roles: string[] };

function readable(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "The payment could not be loaded.";
}

export default function PaymentReceiptPage() {
  const { paymentId = "" } = useParams();
  const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me") });
  const payment = useQuery({
    queryKey: ["payment", paymentId],
    queryFn: () => getPayment(paymentId),
    enabled: Boolean(paymentId),
    retry: false,
  });

  if (session.isPending || payment.isPending) return <Skeleton className="h-64 w-full" />;
  if (session.isError || payment.isError || !payment.data) {
    return (
      <EmptyState
        title="Payment could not be loaded"
        detail={readable(payment.error ?? session.error)}
      />
    );
  }

  const record = payment.data;
  const canPrintReceipt = ["received", "confirmed", "banked"].includes(record.status);
  const paymentRow: PaymentListItem = {
    id: record.id,
    member_id: record.member_id,
    member_no: record.member_no,
    member_name: record.member_name,
    loan_id: record.loan_id,
    loan_name: null,
    purpose: record.purpose,
    amount_kobo: record.amount_kobo,
    channel: record.channel,
    status: record.status,
    recorded_by: record.recorded_by,
    recorded_by_name: record.recorded_by_name,
    rejected_reason: record.rejected_reason,
    recorded_at: record.recorded_at,
    received_on: record.received_on,
    receipt_no: record.receipt_no,
  };

  return (
    <>
      <style>
        {
          "@media print { body { background: white !important; } .no-print { display: none !important; } .receipt-sheet { border: 0 !important; box-shadow: none !important; max-width: 100% !important; } }"
        }
      </style>
      <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
        <PageHeader
          title={canPrintReceipt ? "Payment receipt" : "Payment details"}
          breadcrumb="Payments"
        />
        {canPrintReceipt && (
          <Button type="button" onClick={() => window.print()}>
            Print receipt
          </Button>
        )}
      </div>
      {record.status === "pending" && record.channel === "bank_transfer" && session.data && (
        <section className="mb-5 rounded-card border border-border bg-surface p-4">
          <h2 className="font-semibold">Transfer awaiting confirmation</h2>
          <p className="mt-1 text-sm text-muted">
            Only a different officer or treasurer may confirm or reject this transfer.
          </p>
          <div className="mt-3">
            <PaymentDecisionActions
              payment={paymentRow}
              userId={session.data.user_id}
              roles={session.data.roles}
            />
          </div>
        </section>
      )}
      {record.status === "rejected" && session.data.user_id === record.recorded_by && (
        <p className="mb-4 rounded-control bg-amber-50 p-3 text-sm text-amber-950" role="status">
          Transfer rejected: {record.rejected_reason || "No reason was provided."}
        </p>
      )}
      {canPrintReceipt && record.receipt_no !== null ? (
        <article className="receipt-sheet mx-auto max-w-2xl rounded-card border border-border bg-surface p-8 shadow-card">
          <header className="border-b border-border pb-5 text-center">
            <p className="text-xs uppercase tracking-[0.18em] text-muted">
              Ajo · Cooperative payment
            </p>
            <h1 className="mt-2 font-serif text-3xl font-semibold">Official receipt</h1>
            <p className="mt-2 text-lg font-semibold">Receipt no. {record.receipt_no}</p>
          </header>
          <dl className="mt-6 grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase text-muted">Member</dt>
              <dd className="mt-1 font-medium">{record.member_name}</dd>
              <dd className="text-sm text-muted">Member no. {record.member_no}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Amount received</dt>
              <dd className="mt-1 text-xl font-semibold">
                <Money kobo={record.amount_kobo} />
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Purpose</dt>
              <dd className="mt-1">{record.purpose.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Channel</dt>
              <dd className="mt-1">{record.channel.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Date received</dt>
              <dd className="mt-1">
                <DateReceived value={record.received_on} />
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Status</dt>
              <dd className="mt-1">
                <Badge status={record.status} />
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Recorded by</dt>
              <dd className="mt-1">{record.recorded_by_name}</dd>
            </div>
            {record.confirmed_at && (
              <div>
                <dt className="text-xs uppercase text-muted">Confirmed</dt>
                <dd className="mt-1">
                  <DateReceived value={record.confirmed_at} />
                </dd>
              </div>
            )}
          </dl>
          <footer className="mt-8 border-t border-border pt-4 text-center text-xs text-muted">
            This receipt confirms that the cooperative recorded the payment shown above. Ajo does
            not hold or move funds.
          </footer>
        </article>
      ) : canPrintReceipt ? (
        <EmptyState
          title="Receipt number unavailable"
          detail="A receipt number has not been assigned to this payment."
        />
      ) : record.status !== "pending" && record.status !== "rejected" ? (
        <EmptyState
          title="Receipt unavailable"
          detail="A receipt is available only after the payment is received or confirmed."
        />
      ) : (
        <section className="rounded-card border border-border bg-surface p-5">
          <div className="flex items-center justify-between gap-3">
            <h1 className="font-serif text-xl font-semibold">Payment details</h1>
            <Badge status={record.status} />
          </div>
          <p className="mt-3">
            {record.member_name} · {record.member_no}
          </p>
          <p className="mt-1">
            <Money kobo={record.amount_kobo} /> · {record.channel.replaceAll("_", " ")}
          </p>
          <p className="mt-1 text-sm text-muted">
            Recorded by {record.recorded_by_name} on <DateReceived value={record.received_on} />
          </p>
          <Link className="mt-4 inline-block text-primary underline" to="/payments?action=needed">
            Return to payment actions
          </Link>
        </section>
      )}
      <div className="no-print mt-5">
        <Link className="text-primary underline" to="/payments">
          Back to Payments
        </Link>
      </div>
    </>
  );
}
