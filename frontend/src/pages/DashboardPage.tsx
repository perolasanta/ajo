import { useQuery } from "@tanstack/react-query";
import { Link, Navigate } from "react-router-dom";
import { ApiError, getDashboardCardCount, request } from "../api/client";
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
    queryFn: async () => {
      const counts = await Promise.all(
        cards.map(async (card) => [card.href, await getDashboardCardCount(card)] as const),
      );
      return new Map(counts);
    },
  });

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
      {cards.every((card) => dashboard.data.get(card.href) === 0) && (
        <div className="mb-4">
          <EmptyState title="Everything is up to date" detail="There are no items in the queues available to your role." />
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => (
          <DashboardCard key={card.href} card={card} count={dashboard.data.get(card.href) ?? 0} />
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
