import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError, getDisbursement } from "../api/client";
import type { DisbursementRecord } from "../api/schema";
import { eventsForDisbursement } from "../disbursementTimeline.js";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
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
export default function DisbursementDetailPage() {
  const { disbursementId = "" } = useParams();
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
        </div>
      )}
    </>
  );
}
