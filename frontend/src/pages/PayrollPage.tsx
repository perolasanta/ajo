import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError,
  createPayrollProfile,
  createPayrollRun,
  getNotificationSummary,
  getPayrollProfiles,
  getPayrollRuns,
  request,
} from "../api/client";
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
    key: "action",
    header: "Action",
    role: "meta",
    render: () => <span className="font-medium text-amber-800">Needs my action</span>,
  },
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
  const [searchParams, setSearchParams] = useSearchParams();
  const action = searchParams.get("action") === "needed" ? "needed" : undefined;
  const userSelectedActionTab = useRef(false);
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [showProfileForm, setShowProfileForm] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [staffNoColumn, setStaffNoColumn] = useState("");
  const [amountColumn, setAmountColumn] = useState("");
  const [nameColumn, setNameColumn] = useState("");
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
  const roles = session.data?.roles ?? [];
  const canCreate = canCreatePayrollRun(roles);
  const isAdmin = roles.includes("admin");
  const profiles = useQuery({
    queryKey: ["payroll-profiles"],
    queryFn: getPayrollProfiles,
    enabled: canCreate || isAdmin,
    retry: false,
  });
  const selectedProfileId = profileId || profiles.data?.items[0]?.id || "";
  const createRun = useMutation({
    mutationFn: () =>
      createPayrollRun({
        profile_id: selectedProfileId,
        period_year: Number(periodYear),
        period_month: Number(periodMonth),
      }),
    onSuccess: ({ run_id }) => {
      void queryClient.invalidateQueries({ queryKey: ["payroll-runs"] });
      navigate(`/payroll/runs/${run_id}`);
    },
  });
  const createProfile = useMutation({
    mutationFn: () =>
      createPayrollProfile({
        staff_no_column: staffNoColumn.trim(),
        amount_column: amountColumn.trim(),
        name_column: nameColumn.trim() || null,
      }),
    onSuccess: ({ profile_id }) => {
      setProfileId(profile_id);
      setStaffNoColumn("");
      setAmountColumn("");
      setNameColumn("");
      setShowProfileForm(false);
      void queryClient.invalidateQueries({ queryKey: ["payroll-profiles"] });
    },
  });
  const summary = useQuery({ queryKey: ["notification-summary"], queryFn: getNotificationSummary });
  const actionCount = (isAdmin ? summary.data?.payroll?.awaiting_sign_off ?? 0 : 0) + (roles.includes("treasurer") ? summary.data?.payroll?.awaiting_posting ?? 0 : 0);
  useEffect(() => { if (!userSelectedActionTab.current && actionCount > 0 && !searchParams.has("action")) { const next = new URLSearchParams(searchParams); next.set("action", "needed"); setSearchParams(next, { replace: true }); } }, [actionCount, searchParams, setSearchParams]);
  const runs = useQuery({
    queryKey: ["payroll-runs", page, action],
    queryFn: () => getPayrollRuns({ page, pageSize, action }),
  });
  const state = payrollListState(runs);
  const totalPages = Math.max(1, Math.ceil((runs.data?.total ?? 0) / pageSize));

  return (
    <>
      <PageHeader title="Payroll" breadcrumb="Monthly deduction runs">
        <div className="flex flex-wrap gap-2">
          {isAdmin && (
            <Button type="button" onClick={() => setShowProfileForm((value) => !value)}>
              {showProfileForm ? "Close profile form" : "Create payroll profile"}
            </Button>
          )}
          {canCreate && (
            <Button type="button" onClick={() => setShowCreate((value) => !value)}>
              {showCreate ? "Close run form" : "Create run"}
            </Button>
          )}
        </div>
      </PageHeader>
      <p className="mb-5 max-w-2xl text-sm text-muted">
        Review expected and imported payroll totals. Member salary, contact, and bank details are
        not shown here.
      </p>
      <div className="mb-4 flex gap-2" role="tablist" aria-label="Payroll list view"><Button role="tab" aria-selected={action === "needed"} variant={action === "needed" ? "primary" : "secondary"} onClick={() => { userSelectedActionTab.current = true; const next = new URLSearchParams(searchParams); next.set("action", "needed"); setSearchParams(next); }}>Needs my action{actionCount ? ` (${actionCount})` : ""}</Button><Button role="tab" aria-selected={!action} variant={!action ? "primary" : "secondary"} onClick={() => { userSelectedActionTab.current = true; const next = new URLSearchParams(searchParams); next.delete("action"); setSearchParams(next); }}>All</Button></div>
      {showProfileForm && isAdmin && (
        <form
          className="mb-6 rounded-card border border-border bg-surface p-4 sm:p-5"
          onSubmit={(event) => {
            event.preventDefault();
            createProfile.mutate();
          }}
        >
          <h2 className="font-serif text-xl font-semibold">Create payroll profile</h2>
          <p className="mt-1 text-sm text-muted">
            Match these fields to the column headers in your payroll CSV.
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <label className="text-sm font-medium">
              Staff number column header
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                required
                maxLength={100}
                value={staffNoColumn}
                onChange={(event) => setStaffNoColumn(event.target.value)}
                placeholder="Staff No"
              />
            </label>
            <label className="text-sm font-medium">
              Amount column header
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                required
                maxLength={100}
                value={amountColumn}
                onChange={(event) => setAmountColumn(event.target.value)}
                placeholder="Amount"
              />
            </label>
            <label className="text-sm font-medium">
              Name column header (optional)
              <input
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                maxLength={100}
                value={nameColumn}
                onChange={(event) => setNameColumn(event.target.value)}
                placeholder="Full Name"
              />
            </label>
          </div>
          {createProfile.isError && (
            <p className="mt-3 text-sm text-danger" role="alert">
              {createProfile.error instanceof ApiError
                ? createProfile.error.message
                : "Unable to create the payroll profile. Check the column headers and try again."}
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button type="submit" loading={createProfile.isPending}>
              Save profile
            </Button>
            <Button type="button" variant="secondary" onClick={() => setShowProfileForm(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {createProfile.isSuccess && (
        <p className="mb-4 text-sm text-success" role="status">
          Payroll profile created successfully.
        </p>
      )}
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
            Choose a saved payroll profile and the period for this run.
          </p>
          {profiles.isPending && (
            <p className="mt-3 text-sm text-muted" role="status">
              Loading payroll profiles…
            </p>
          )}
          {profiles.isError && (
            <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-danger" role="alert">
              {profiles.error instanceof ApiError
                ? profiles.error.message
                : "Payroll profiles could not be loaded."}
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => void profiles.refetch()}
              >
                Try again
              </Button>
            </p>
          )}
          {profiles.data?.items.length === 0 && (
            <p className="mt-3 text-sm text-muted" role="status">
              No payroll profiles yet. An admin has to create one.
            </p>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <label className="text-sm font-medium">
              Payroll profile
              <select
                className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-sm"
                required
                disabled={profiles.isPending || profiles.isError || !profiles.data?.items.length}
                value={selectedProfileId}
                onChange={(event) => setProfileId(event.target.value)}
              >
                <option value="" disabled>
                  Select a profile
                </option>
                {profiles.data?.items.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} ({profile.staff_no_column}, {profile.amount_column}
                    {profile.name_column ? `, ${profile.name_column}` : ""})
                  </option>
                ))}
              </select>
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
            <Button
              type="submit"
              loading={createRun.isPending}
              disabled={!selectedProfileId || profiles.isError || profiles.isPending}
            >
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
            columns={action ? columns : columns.slice(1)}
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
