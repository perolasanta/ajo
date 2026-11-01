import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { getPayrollRuns, ApiError } from "../api/client";
import type { PayrollRunSummary } from "../api/schema";
import { payrollListState } from "../payrollPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";

const pageSize = 10;

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
  const runs = useQuery({
    queryKey: ["payroll-runs", page],
    queryFn: () => getPayrollRuns({ page, pageSize }),
  });
  const state = payrollListState(runs);
  const totalPages = Math.max(1, Math.ceil((runs.data?.total ?? 0) / pageSize));

  return (
    <>
      <PageHeader title="Payroll" breadcrumb="Monthly deduction runs" />
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Review expected and imported payroll totals. Member salary, contact, and bank details are
        not shown here.
      </p>
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
