import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import { ApiError, getDisbursements } from "../api/client";
import type { DisbursementRecord } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";

const pageSize = 10;
function useDebouncedValue(value: string, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);
  return debounced;
}
function ListError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  return (
    <EmptyState
      title="Disbursements could not be loaded"
      detail={`${message}${error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}
const columns: ResponsiveColumn<DisbursementRecord>[] = [
  {
    key: "member",
    header: "Member",
    role: "title",
    render: (item) => (
      <Link className="font-medium text-primary hover:underline" to={`/disbursements/${item.id}`}>
        {item.full_name}
        <span className="mt-1 block text-xs font-normal text-muted">{item.member_no}</span>
      </Link>
    ),
  },
  { key: "amount", header: "Amount", role: "meta", numeric: true, render: (item) => <Money kobo={item.amount_kobo} /> },
  { key: "status", header: "Status", role: "badge", render: (item) => <Badge status={item.status} /> },
  { key: "date", header: "Date", role: "meta", render: (item) => <DateTime value={item.created_at} /> },
];
function ListSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading disbursements">
      {[0, 1, 2].map((index) => <Skeleton key={index} className="h-[88px] w-full" />)}
    </div>
  );
}
export default function DisbursementsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search);
  const disbursements = useQuery({
    queryKey: ["disbursements", debouncedSearch, status, page],
    queryFn: () => getDisbursements({ search: debouncedSearch || undefined, status, page, pageSize }),
  });
  const statuses = useMemo(() => [...new Set((disbursements.data?.items ?? []).map((item) => item.status))], [disbursements.data]);
  const totalPages = Math.max(1, Math.ceil((disbursements.data?.total ?? 0) / pageSize));
  useEffect(() => setPage(1), [debouncedSearch, status]);
  return (
    <>
      <PageHeader title="Disbursements" breadcrumb="Manual bank transfers" />
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Transfers are made manually outside Ajo. This register records preparation, authorization, and transfer confirmation; Ajo does not send money.
      </p>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input aria-label="Search disbursements" placeholder="Search member name or number." value={search} onChange={(event) => setSearch(event.target.value)} />
        <Select aria-label="Filter disbursements by status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="all">All statuses</option>
          {statuses.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
        </Select>
      </div>
      {disbursements.isPending && <ListSkeleton />}
      {disbursements.isError && <ListError error={disbursements.error} retry={() => void disbursements.refetch()} />}
      {disbursements.data && disbursements.data.items.length === 0 && (
        <EmptyState title="No disbursements found" detail="Try another search or status filter." action={
          <Button variant="secondary" onClick={() => { setSearch(""); setStatus("all"); }}>Clear filters</Button>
        } />
      )}
      {disbursements.data && disbursements.data.items.length > 0 && (
        <>
          <ResponsiveTable data={disbursements.data.items} columns={columns} getRowKey={(item) => item.id} />
          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span>{disbursements.data.total} disbursements</span>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</Button>
              <span>Page {page} of {totalPages}</span>
              <Button variant="secondary" size="sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Next</Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
