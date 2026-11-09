import { useQuery } from "@tanstack/react-query";
import { Link, Navigate } from "react-router-dom";
import { ApiError, getDashboardSummary, getNotificationSummary, request } from "../api/client";
import { dashboardCards } from "../dashboardPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { PageHeader } from "../ui/PageHeader";

type Session = { roles: string[] };

type DashboardCard = ReturnType<typeof dashboardCards>[number];

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Dashboard data could not be loaded.";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <EmptyState
      title="Dashboard data could not be loaded"
      detail={`${message}${requestId ? ` Request ID: ${requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function DashboardSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="Loading dashboard">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-36" />
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const roles = session.data?.roles ?? [];
  const cards = dashboardCards(roles);
  const dashboard = useQuery({
    queryKey: ["dashboard", roles],
    enabled: session.isSuccess,
    queryFn: getDashboardSummary,
  });
  const actionSummary = useQuery({
    queryKey: ["notification-summary"],
    enabled: session.isSuccess,
    queryFn: getNotificationSummary,
  });
  const countForCard = (card: DashboardCard) => {
    if (card.actionType === "payroll")
      return (roles.includes("treasurer")
        ? actionSummary.data?.payroll?.awaiting_posting ?? 0
        : 0) + (roles.includes("admin")
        ? actionSummary.data?.payroll?.awaiting_sign_off ?? 0
        : 0);
    if (card.actionType === "withdrawals")
      return actionSummary.data?.withdrawal_requests?.awaiting_approval ?? 0;
    if (card.actionType === "role_changes")
      return actionSummary.data?.role_changes?.awaiting_approval ?? 0;
    if (card.actionType === "disbursements")
      return (actionSummary.data?.disbursements?.awaiting_authorization ?? 0) +
        (actionSummary.data?.disbursements?.awaiting_confirmation ?? 0) +
        (actionSummary.data?.disbursements?.ready_to_record ?? 0);
    if (card.actionType === "loans")
      return (actionSummary.data?.loans?.awaiting_decision ?? 0) +
        (actionSummary.data?.loans?.disputed_or_paused ?? 0);
    if (card.source === "action") return 0;
    return dashboard.data?.[card.source]?.[card.status] ?? 0;
  };

  if (session.isPending) return <DashboardSkeleton />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  if (dashboard.isPending) {
    return (
      <>
        <PageHeader title="Dashboard" breadcrumb="Cooperative overview" />
        <DashboardSkeleton />
      </>
    );
  }
  if (dashboard.isError) return <ErrorState error={dashboard.error} retry={() => void dashboard.refetch()} />;
  if (cards.length === 0) {
    return (
      <>
        <PageHeader title="Dashboard" breadcrumb="Cooperative overview" />
        <EmptyState title="No dashboard summary available" detail="Your role has no dashboard cards configured." />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Dashboard" breadcrumb="Cooperative overview" />
      {cards.every((card) => countForCard(card) === 0) && (
        <div className="mb-4">
          <EmptyState title="Everything is up to date" detail="There are no items in the queues available to your role." />
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => (
          <DashboardCard key={card.href} card={card} count={countForCard(card)} />
        ))}
      </div>
    </>
  );
}

function DashboardCard({ card, count }: { card: DashboardCard; count: number }) {
  return (
    <Link to={card.href} className="block rounded-card outline-offset-4 focus-visible:outline-2 focus-visible:outline-primary">
      <Card className="h-full transition hover:-translate-y-0.5 hover:border-primary/40">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm text-muted">{card.description}</p>
          <Badge>{card.status.replaceAll("_", " ")}</Badge>
        </div>
        <p className="mt-5 font-serif text-4xl font-semibold tracking-[-0.04em]">{count}</p>
        <h2 className="mt-1 text-sm font-medium">{card.title}</h2>
        <p className="mt-4 text-xs text-muted">Open filtered list</p>
      </Card>
    </Link>
  );
}
