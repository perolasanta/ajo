import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { ApiError, getAuditEvents, request, verifyAuditChain } from "../api/client";
import type { AuditEvent } from "../api/audit";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Overlay";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";

const pageSize = 25;
function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  const message = error instanceof ApiError ? error.message : "Audit events could not be loaded.";
  return (
    <EmptyState
      title="Audit events could not be loaded"
      detail={requestId ? `${message} Request ID: ${requestId}` : message}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

function summaryText(event: AuditEvent) {
  const values = Object.entries(event.safe_summary).filter(([, value]) => value !== null);
  return values.length > 0 ? values.map(([key, value]) => `${key}: ${value}`).join(" · ") : "No safe details";
}

export default function AuditPage() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<{ roles: string[] }>("/auth/me"),
    retry: false,
  });
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [actorId, setActorId] = useState("");
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const audit = useQuery({
    queryKey: ["audit-events", { from, to, actorId, action, entityType, page }],
    queryFn: () =>
      getAuditEvents({
        from: from ? `${from}T00:00:00Z` : undefined,
        to: to ? `${to}T23:59:59.999Z` : undefined,
        actorId: actorId || undefined,
        action: action || undefined,
        entityType: entityType || undefined,
        page,
        pageSize,
      }),
  });
  const verification = useQuery({ queryKey: ["audit-verify"], queryFn: verifyAuditChain, enabled: false });
  const events = audit.data?.items ?? [];
  const totalPages = Math.max(1, Math.ceil((audit.data?.total ?? 0) / pageSize));
  const entityTypes = useMemo(
    () => [...new Set(events.map((event) => event.entity_type))].sort(),
    [events],
  );
  useEffect(() => setPage(1), [from, to, actorId, action, entityType]);

  if (session.isPending) return <Skeleton className="h-60" />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  if (!session.data.roles.some((role) => role === "admin" || role === "auditor")) {
    return <Navigate to="/dashboard" replace />;
  }
  return (
    <>
      <PageHeader title="Audit log" breadcrumb="Read-only evidence trail">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void verification.refetch()}
          disabled={verification.isFetching}
        >
          {verification.isFetching ? "Verifying…" : "Verify chain"}
        </Button>
      </PageHeader>
      {verification.data && (
        <p className="mb-4 text-sm" role="status">
          {verification.data.ok
            ? `Chain verified across ${verification.data.entries_checked} events.`
            : `Chain broken at position ${verification.data.first_broken_position}.`}
        </p>
      )}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <label className="text-sm font-medium">From<input className="mt-1.5 block w-full rounded-control border border-border bg-surface px-3 py-2" type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label className="text-sm font-medium">To<input className="mt-1.5 block w-full rounded-control border border-border bg-surface px-3 py-2" type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        <Input aria-label="Filter by actor" placeholder="Actor ID" value={actorId} onChange={(event) => setActorId(event.target.value)} />
        <Input aria-label="Filter by action" placeholder="Action" value={action} onChange={(event) => setAction(event.target.value)} />
        <Select aria-label="Filter by entity type" value={entityType} onChange={(event) => setEntityType(event.target.value)}>
          <option value="">All entity types</option>
          {entityTypes.map((value) => <option key={value}>{value}</option>)}
        </Select>
      </div>
      {audit.isPending && <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>}
      {audit.isError && <ErrorState error={audit.error} retry={() => void audit.refetch()} />}
      {audit.data && events.length === 0 && <EmptyState title="No audit events found" detail="Try a wider date range or clear a filter." action={<Button variant="secondary" onClick={() => { setFrom(""); setTo(""); setActorId(""); setAction(""); setEntityType(""); }}>Clear filters</Button>} />}
      {events.length > 0 && (
        <>
          <div className="overflow-hidden rounded-card border border-border bg-surface">
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="border-b border-border bg-[#FAF9F6] text-[11px] uppercase tracking-[0.12em] text-muted"><tr><th className="px-3 py-3">When</th><th className="px-3 py-3">Action</th><th className="px-3 py-3">Entity</th><th className="px-3 py-3">Actor</th><th className="px-3 py-3">Summary</th></tr></thead>
                <tbody>{events.map((event) => <EventRow key={event.event_id} event={event} onOpen={setSelected} />)}</tbody>
              </table>
            </div>
            <div className="space-y-3 p-3 md:hidden">{events.map((event) => <button type="button" key={event.event_id} onClick={() => setSelected(event)} className="block w-full rounded-card border border-border p-4 text-left"><div className="flex items-start justify-between gap-3"><span className="font-medium">{event.action}</span><Badge>{event.entity_type}</Badge></div><p className="mt-2 text-xs text-muted">{formatTimestamp(event.timestamp)}</p><p className="mt-2 text-sm">{summaryText(event)}</p></button>)}</div>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"><span>{audit.data.total} events</span><div className="flex items-center gap-2"><Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</Button><span>Page {page} of {totalPages}</span><Button variant="secondary" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button></div></div>
        </>
      )}
      <Dialog open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }} title="Audit event" description="Read-only event details">
        {selected && <dl className="space-y-3 text-sm"><Detail label="Event ID" value={selected.event_id} /><Detail label="Timestamp" value={formatTimestamp(selected.timestamp)} /><Detail label="Actor" value={selected.actor_id ?? "System"} /><Detail label="Role" value={selected.actor_role ?? "—"} /><Detail label="Action" value={selected.action} /><Detail label="Entity" value={`${selected.entity_type} · ${selected.entity_id ?? "—"}`} /><Detail label="Safe summary" value={summaryText(selected)} /></dl>}
      </Dialog>
    </>
  );
}

function EventRow({ event, onOpen }: { event: AuditEvent; onOpen: (event: AuditEvent) => void }) {
  return <tr className="cursor-pointer hover:bg-[#F7F7F1]" onClick={() => onOpen(event)}><td className="border-b border-border px-3 py-3 whitespace-nowrap">{formatTimestamp(event.timestamp)}</td><td className="border-b border-border px-3 py-3 font-medium">{event.action}</td><td className="border-b border-border px-3 py-3">{event.entity_type}<span className="block text-xs text-muted">{event.entity_id ?? "—"}</span></td><td className="border-b border-border px-3 py-3">{event.actor_role ?? "System"}</td><td className="border-b border-border px-3 py-3 text-muted">{summaryText(event)}</td></tr>;
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs uppercase tracking-[0.1em] text-muted">{label}</dt><dd className="mt-1 break-words">{value}</dd></div>;
}
