import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  getDisbursements,
  getReadyDisbursementLoans,
  getNotificationSummary,
  prepareDisbursement,
  request,
} from "../api/client";
import type { DisbursementRecord } from "../api/schema";
import type { ReadyDisbursementLoan } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";
import { canPrepareDisbursement } from "../disbursementPolicy.mjs";
import { disbursementListState } from "../disbursementListState.mjs";

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
  {
    key: "amount",
    header: "Amount",
    role: "meta",
    numeric: true,
    render: (item) => <Money kobo={item.amount_kobo} />,
  },
  {
    key: "status",
    header: "Status",
    role: "badge",
    render: (item) => <Badge status={item.status} />,
  },
  {
    key: "date",
    header: "Date",
    role: "meta",
    render: (item) => <DateTime value={item.created_at} />,
  },
];
function ListSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading disbursements">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-[88px] w-full" />
      ))}
    </div>
  );
}
export default function DisbursementsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(() => searchParams.get("status") ?? "all");
  const action = searchParams.get("action") === "needed" ? "needed" : undefined;
  const userSelectedActionTab = useRef(false);
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search);
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<{ user_id: string; roles: string[] }>("/auth/me"),
  });
  const canPrepare = canPrepareDisbursement(session.data?.roles ?? []);
  const readyLoans = useQuery({
    queryKey: ["ready-disbursement-loans"],
    queryFn: () => getReadyDisbursementLoans(),
    enabled: canPrepare,
  });
  const prepare = useMutation({
    mutationFn: (loanId: string) => prepareDisbursement(loanId),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["ready-disbursement-loans"] });
      await queryClient.invalidateQueries({ queryKey: ["disbursements"] });
      navigate(`/disbursements/${result.disbursement_id}`);
    },
  });
  const summary = useQuery({ queryKey: ["notification-summary"], queryFn: getNotificationSummary });
  const roles = session.data?.roles ?? [];
  const actionCount = (roles.includes("admin") ? (summary.data?.disbursements?.awaiting_authorization ?? 0) + (summary.data?.disbursements?.awaiting_confirmation ?? 0) : 0) + (roles.includes("treasurer") ? summary.data?.disbursements?.ready_to_record ?? 0 : 0);
  useEffect(() => { if (!userSelectedActionTab.current && actionCount > 0 && !searchParams.has("action")) { const next = new URLSearchParams(searchParams); next.set("action", "needed"); setSearchParams(next, { replace: true }); } }, [actionCount, searchParams, setSearchParams]);
  const disbursements = useQuery({
    queryKey: ["disbursements", debouncedSearch, status, action, page],
    queryFn: () =>
      getDisbursements({ search: debouncedSearch || undefined, status, action, page, pageSize }),
  });
  const listState = disbursementListState(disbursements);
  const statuses = useMemo(
    () => [
      ...new Set([
        ...(disbursements.data?.items ?? []).map((item) => item.status),
        ...(status === "all" ? [] : [status]),
      ]),
    ],
    [disbursements.data, status],
  );
  const totalPages = Math.max(1, Math.ceil((disbursements.data?.total ?? 0) / pageSize));
  useEffect(() => setPage(1), [debouncedSearch, status, action]);
  useEffect(() => setStatus(searchParams.get("status") ?? "all"), [searchParams]);

  function updateStatus(value: string) {
    setStatus(value);
    const next = new URLSearchParams(searchParams);
    if (value === "all") next.delete("status");
    else next.set("status", value);
    setSearchParams(next, { replace: true });
  }
  return (
    <>
      <PageHeader title="Disbursements" breadcrumb="Manual bank transfers" />
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Transfers are made manually outside Ajo. This register records preparation, authorization,
        and transfer confirmation; Ajo does not send money.
      </p>
      {canPrepare && (
        <section className="mb-6 rounded-card border border-border bg-surface p-4 sm:p-5">
          <h2 className="font-serif text-xl font-semibold">Loans ready for preparation</h2>
          <p className="mt-1 text-sm text-muted">
            Select an approved loan to prepare its manual bank transfer.
          </p>
          {readyLoans.isPending && <Skeleton className="mt-4 h-20 w-full" />}
          {readyLoans.isError && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {readyLoans.error instanceof ApiError
                ? readyLoans.error.message
                : "Ready loans could not be loaded."}
            </p>
          )}
          {readyLoans.data && readyLoans.data.items.length === 0 && (
            <p className="mt-4 text-sm text-muted">No loans are ready for disbursement.</p>
          )}
          {readyLoans.data?.items.map((loan) => (
            <ReadyLoanRow
              key={loan.loan_id}
              loan={loan}
              pending={prepare.isPending}
              onPrepare={() => {
                if (
                  window.confirm(
                    `Prepare the manual transfer for ${loan.full_name} (${loan.member_no})? Ajo will not send money.`,
                  )
                )
                  prepare.mutate(loan.loan_id);
              }}
            />
          ))}
          {prepare.isError && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {prepare.error instanceof ApiError
                ? prepare.error.message
                : "Preparation failed. Please try again."}
            </p>
          )}
        </section>
      )}
      <div className="mb-4 flex gap-2" role="tablist" aria-label="Disbursement list view"><Button role="tab" aria-selected={action === "needed"} variant={action === "needed" ? "primary" : "secondary"} onClick={() => { userSelectedActionTab.current = true; const next = new URLSearchParams(searchParams); next.set("action", "needed"); setSearchParams(next); }}>Needs my action{actionCount ? ` (${actionCount})` : ""}</Button><Button role="tab" aria-selected={!action} variant={!action ? "primary" : "secondary"} onClick={() => { userSelectedActionTab.current = true; const next = new URLSearchParams(searchParams); next.delete("action"); setSearchParams(next); }}>All</Button></div>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          aria-label="Search disbursements"
          placeholder="Search member name or number."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          aria-label="Filter disbursements by status"
          value={status}
          onChange={(event) => updateStatus(event.target.value)}
        >
          <option value="all">All statuses</option>
          {statuses.map((value) => (
            <option key={value} value={value}>
              {value.replaceAll("_", " ")}
            </option>
          ))}
        </Select>
      </div>
      {listState === "loading" && <ListSkeleton />}
      {listState === "error" && (
        <ListError error={disbursements.error} retry={() => void disbursements.refetch()} />
      )}
      {listState === "empty" && (
        <EmptyState
          title="No disbursements found"
          detail="Try another search or status filter."
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setSearch("");
                setStatus("all");
              }}
            >
              Clear filters
            </Button>
          }
        />
      )}
      {listState === "results" && disbursements.data && (
        <>
          <ResponsiveTable
            data={disbursements.data.items}
            columns={action ? [{ key: "action", header: "Action", role: "meta", render: () => <span className="font-medium text-amber-800">Needs my action</span> }, ...columns] : columns}
            getRowKey={(item) => item.id}
          />
          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span>{disbursements.data.total} disbursements</span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous
              </Button>
              <span>
                Page {page} of {totalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function ReadyLoanRow({
  loan,
  pending,
  onPrepare,
}: {
  loan: ReadyDisbursementLoan;
  pending: boolean;
  onPrepare: () => void;
}) {
  return (
    <div className="mt-3 flex flex-col gap-3 rounded-control border border-border p-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="font-medium">
          {loan.full_name} <span className="font-normal text-muted">{loan.member_no}</span>
        </p>
        <p className="mt-1 text-sm text-muted">
          <Money kobo={loan.amount_kobo} />
        </p>
      </div>
      <Button disabled={pending} onClick={onPrepare}>
        {pending ? "Preparing…" : "Prepare transfer"}
      </Button>
    </div>
  );
}
