import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, createPayrollRun, getPayrollRuns, request } from "../api/client";
import type { PayrollRunSummary } from "../api/schema";
import { canCreatePayrollRun, payrollListState } from "../payrollPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";

const pageSize = 10;
type Session = { roles: string[] };

function ListError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  return (
    <EmptyState
      title="Payroll runs could not be loaded"
      detail={`${message}${error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

const columns: ResponsiveColumn<PayrollRunSummary>[] = [
  {
    key: "period",
    header: "Period",
    role: "title",
    render: (run) => (
      <Link className="font-medium text-primary hover:underline" to={`/payroll/runs/${run.run_id}`}>
        {run.period_year}-{String(run.period_month).padStart(2, "0")}
      </Link>
    ),
  },
  {
    key: "status",
    header: "Status",
    role: "badge",
    render: (run) => <Badge status={run.status} />,
  },
  {
    key: "expected",
    header: "Expected total",
    role: "meta",
    numeric: true,
    render: (run) => <Money kobo={run.expected_total_kobo} />,
  },
  {
    key: "file",
    header: "File total",
    role: "meta",
    numeric: true,
    render: (run) =>
      run.file_total_kobo === null ? (
        <span className="text-muted">Not imported</span>
      ) : (
        <Money kobo={run.file_total_kobo} />
      ),
  },
  {
    key: "variance",
    header: "File variance",
    role: "meta",
    numeric: true,
    render: (run) => {
      if (run.file_total_kobo === null) return <span className="text-muted">—</span>;
      return <Money kobo={run.file_total_kobo - run.expected_total_kobo} />;
    },
  },
];

function ListSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading payroll runs">
      {[0, 1, 2].map((index) => (
        <Skeleton key={index} className="h-[88px] w-full" />
      ))}
    </div>
  );
}

export default function PayrollPage() {
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [profileId, setProfileId] = useState("");
  const now = new Date();
  const [periodYear, setPeriodYear] = useState(String(now.getFullYear()));
  const [periodMonth, setPeriodMonth] = useState(String(now.getMonth() + 1));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const canCreate = canCreatePayrollRun(session.data?.roles ?? []);
  const createRun = useMutation({
    mutationFn: () =>
      createPayrollRun({
        profile_id: profileId.trim(),
        period_year: Number(periodYear),
        period_month: Number(periodMonth),
      }),
    onSuccess: ({ run_id }) => {
      void queryClient.invalidateQueries({ queryKey: ["payroll-runs"] });
      navigate(`/payroll/runs/${run_id}`);
    },
  });
  const runs = useQuery({
    queryKey: ["payroll-runs", page],
    queryFn: () => getPayrollRuns({ page, pageSize }),
  });
  const state = payrollListState(runs);
  const totalPages = Math.max(1, Math.ceil((runs.data?.total ?? 0) / pageSize));

  return (
    <>
      <PageHeader title="Payroll" breadcrumb="Monthly deduction runs">
        {canCreate && (
          <Button type="button" onClick={() => setShowCreate((value) => !value)}>
            {showCreate ? "Close run form" : "Create run"}
          </Button>
        )}
      </PageHeader>
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Review expected and imported payroll totals. Member salary, contact, and bank details are
        not shown here.
      </p>
      {showCreate && canCreate && (
        <form
          className="mb-6 rounded-card border border-border bg-surface p-4 sm:p-5"
          onSubmit={(event) => {
            event.preventDefault();
            createRun.mutate();
          }}
        >
          <h2 className="font-serif text-xl font-semibold">Create payroll run</h2>
          <p className="mt-1 text-sm text-muted">
            Enter the profile ID configured by your payroll administrator. Profile management
            remains admin-only.
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <label className="text-sm font-medium">
              Payroll profile ID
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                required
                minLength={36}
                maxLength={36}
                pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
                placeholder="UUID from your admin"
              />
            </label>
            <label className="text-sm font-medium">
              Period year
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                type="number"
                min={2000}
                max={9999}
                required
                value={periodYear}
                onChange={(event) => setPeriodYear(event.target.value)}
              />
            </label>
            <label className="text-sm font-medium">
              Period month
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                type="number"
                min={1}
                max={12}
                required
                value={periodMonth}
                onChange={(event) => setPeriodMonth(event.target.value)}
              />
            </label>
          </div>
          {createRun.isError && (
            <p className="mt-3 text-sm text-danger" role="alert">
              {createRun.error instanceof ApiError
                ? createRun.error.message
                : "Unable to create the payroll run."}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button type="submit" loading={createRun.isPending}>
              Create run
            </Button>
            <Button type="button" variant="secondary" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {state === "loading" && <ListSkeleton />}
      {state === "error" && <ListError error={runs.error} retry={() => void runs.refetch()} />}
      {state === "empty" && (
        <EmptyState
          title="No payroll runs yet"
          detail="Payroll runs will appear here when they are created."
        />
      )}
      {state === "results" && runs.data && (
        <>
          <ResponsiveTable
            data={runs.data.items}
            columns={columns}
            getRowKey={(run) => run.run_id}
          />
          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span>{runs.data.total} payroll runs</span>
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
