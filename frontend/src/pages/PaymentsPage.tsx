import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, getNotificationSummary, getPayments, request } from "../api/client";
import type { PaymentListItem } from "../api/schema";
import { menuActionCount } from "../navigation";
import { PaymentRecordForm } from "./PaymentRecordForm";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateReceived, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";

type Session = { user_id: string; roles: string[] };
type PaymentsTab = "needed" | "all";

const pageSize = 25;
const recordedTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Africa/Lagos",
});

function formatRecordedTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${recordedTimeFormatter.format(date)} WAT`;
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function PaymentsError({ error, retry }: { error: unknown; retry: () => void }) {
  const detail = error instanceof ApiError ? error.message : "Please try again.";
  const requestId =
    error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : "";
  return (
    <EmptyState
      title="Payments could not be loaded"
      detail={`${detail}${requestId}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function PaymentsSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading payments">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-[88px] w-full" />
      ))}
    </div>
  );
}

const columns: ResponsiveColumn<PaymentListItem>[] = [
  {
    key: "member",
    header: "Member",
    role: "title",
    render: (payment) => (
      <div className="min-w-0">
        <Link
          className="font-medium text-primary hover:underline"
          to={`/members/${payment.member_id}`}
        >
          {payment.member_name}
        </Link>
        <p className="text-xs text-muted">Member no. {payment.member_no}</p>
      </div>
    ),
  },
  {
    key: "status",
    header: "Status",
    role: "badge",
    render: (payment) => <Badge status={payment.status} />,
  },
  {
    key: "amount",
    header: "Amount",
    role: "meta",
    numeric: true,
    render: (payment) => <Money kobo={payment.amount_kobo} />,
  },
  {
    key: "purpose",
    header: "Purpose / loan",
    role: "meta",
    render: (payment) => (
      <span>
        {label(payment.purpose)}
        {payment.loan_name ? ` · ${payment.loan_name}` : ""}
      </span>
    ),
  },
  {
    key: "channel",
    header: "Channel",
    role: "meta",
    render: (payment) => label(payment.channel),
  },
  {
    key: "recorded-by",
    header: "Recorded by",
    role: "meta",
    render: (payment) => payment.recorded_by_name,
  },
  {
    key: "received-on",
    header: "Date received",
    role: "meta",
    render: (payment) => <DateReceived value={payment.received_on} />,
  },
  {
    key: "recorded-at",
    header: "Recorded at",
    role: "meta",
    render: (payment) => (
      <time dateTime={payment.recorded_at}>{formatRecordedTime(payment.recorded_at)}</time>
    ),
  },
  {
    key: "receipt",
    header: "Receipt no.",
    role: "meta",
    render: (payment) => payment.receipt_no ?? "—",
  },
];

export default function PaymentsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedTab, setSelectedTab] = useState<PaymentsTab | null>(null);
  const [page, setPage] = useState(1);
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const summary = useQuery({
    queryKey: ["notification-summary", session.data?.user_id],
    queryFn: getNotificationSummary,
    enabled: Boolean(session.data),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const roles = session.data?.roles ?? [];
  const actionCount = menuActionCount("Payments", summary.data, roles);
  const requestedAction = searchParams.get("action") === "needed";
  useEffect(() => {
    if (requestedAction) {
      setSelectedTab("needed");
      setPage(1);
    }
  }, [requestedAction]);
  const activeTab = selectedTab ?? (requestedAction || actionCount > 0 ? "needed" : "all");
  const payments = useQuery({
    queryKey: ["payments", activeTab, page],
    queryFn: () =>
      getPayments({
        action: activeTab === "needed" ? "needed" : undefined,
        page,
        pageSize,
      }),
    enabled: Boolean(session.data),
    retry: false,
  });
  const totalPages = Math.max(1, Math.ceil((payments.data?.total ?? 0) / pageSize));

  const selectTab = (tab: PaymentsTab) => {
    setSelectedTab(tab);
    setPage(1);
    const next = new URLSearchParams(searchParams);
    if (tab === "needed") next.set("action", "needed");
    else next.delete("action");
    setSearchParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader title="Payments" breadcrumb="Payment records and action queue" />
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Review recorded payments. Bank account numbers are never shown in this list.
      </p>
      <PaymentRecordForm roles={roles} />
      <div className="mb-4 flex flex-wrap gap-2" role="tablist" aria-label="Payments list view">
        <Button
          role="tab"
          aria-selected={activeTab === "needed"}
          variant={activeTab === "needed" ? "primary" : "secondary"}
          onClick={() => selectTab("needed")}
        >
          Needs my action{actionCount > 0 ? ` (${actionCount})` : ""}
        </Button>
        <Button
          role="tab"
          aria-selected={activeTab === "all"}
          variant={activeTab === "all" ? "primary" : "secondary"}
          onClick={() => selectTab("all")}
        >
          All
        </Button>
      </div>

      {session.isPending && <PaymentsSkeleton />}
      {session.isError && (
        <EmptyState
          title="Payments could not be loaded"
          detail="Your session could not be verified. Refresh and try again."
          action={<Button onClick={() => void session.refetch()}>Try again</Button>}
        />
      )}
      {!session.isPending && !session.isError && payments.isPending && <PaymentsSkeleton />}
      {!session.isPending && !session.isError && payments.isError && (
        <PaymentsError error={payments.error} retry={() => void payments.refetch()} />
      )}
      {!session.isPending &&
        !session.isError &&
        payments.data &&
        (payments.data.items.length === 0 ? (
          <EmptyState
            title={activeTab === "needed" ? "Nothing needs your action" : "No payments recorded"}
            detail={
              activeTab === "needed"
                ? "There are no payment actions assigned to you right now."
                : "Payments will appear here after they are recorded."
            }
          />
        ) : (
          <>
            <ResponsiveTable
              data={payments.data.items}
              columns={columns}
              getRowKey={(payment) => payment.id}
            />
            {payments.data.total > 0 && (
              <div className="mt-4 flex items-center justify-between gap-3">
                <p className="text-sm text-muted">
                  Page {page} of {totalPages} · {payments.data.total} payments
                </p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={page <= 1 || payments.isFetching}
                    onClick={() => setPage((current) => current - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={page >= totalPages || payments.isFetching}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        ))}
    </>
  );
}
