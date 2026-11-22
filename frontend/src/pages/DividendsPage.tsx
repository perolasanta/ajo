import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  approveDividendRun,
  cancelDividendRun,
  getDividendRun,
  getDividendPeriods,
  getDividendRuns,
  getMembers,
  prepareDividendRun,
  rejectDividendRun,
  request,
} from "../api/client";
import {
  canApproveDividendRun,
  canCancelDividendRun,
  canPrepareDividendRun,
  canRejectDividendRun,
  memberDisplayLabel,
} from "../dividendsPolicy.mjs";
import { periodsForFrequency, periodBoundaries, recordDateInPeriod } from "../dividendPeriods.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type Session = { user_id: string; roles: string[] };

const PAGE_SIZE = 25;
const numberFormat = new Intl.NumberFormat("en-NG");

/** Format an ISO date string as a readable date, e.g. "31 Dec 2025". */
function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Status labels for display (replace underscores, title-case). */
function statusLabel(status: string): string {
  return status
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export default function DividendsPage() {
  const { runId } = useParams<{ runId?: string }>();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
  });
  const roles = session.data?.roles ?? [];
  const userId = session.data?.user_id;
  if (runId) {
    return <DividendRunDetail runId={runId} roles={roles} userId={userId} />;
  }
  return <DividendRunList roles={roles} />;
}

function DividendRunList({ roles }: { roles: string[] }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const actionNeeded = new URLSearchParams(location.search).get("action") === "needed";
  const [page, setPage] = useState(1);
  const [recordDate, setRecordDate] = useState("");
  const [poolNaira, setPoolNaira] = useState("");
  const [meetingOrResolution, setMeetingOrResolution] = useState("");
  const [selectedPeriodKey, setSelectedPeriodKey] = useState("");
  const effectivePolicyQuery = useQuery({
    queryKey: ["dividend-periods"],
    queryFn: () => getDividendPeriods(new Date().toISOString().slice(0, 10)),
    enabled: canPrepareDividendRun(roles),
  });
  const frequency = effectivePolicyQuery.data?.dividend_frequency;
  const availablePeriods = frequency
    ? periodsForFrequency(frequency as "annual" | "half_yearly" | "quarterly" | "monthly")
    : [];
  // Default record date: end of period or today, whichever is earlier
  const maxRecordDate = (() => {
    if (!selectedPeriodKey) return "";
    const today = new Date().toISOString().slice(0, 10);
    try {
      const { end } = periodBoundaries(selectedPeriodKey);
      const endStr = end.toISOString().slice(0, 10);
      return endStr < today ? endStr : today;
    } catch {
      return today;
    }
  })();

  const runs = useQuery({
    queryKey: ["dividend-runs", page],
    queryFn: () => getDividendRuns({ page, pageSize: PAGE_SIZE }),
  });
  const allItems = runs.data?.items ?? [];
  const items = actionNeeded ? allItems.filter((run) => run.status === "unprocessed") : allItems;
  const totalPages = Math.max(1, Math.ceil((runs.data?.total ?? 0) / PAGE_SIZE));
  const canPrepare = canPrepareDividendRun(roles);

  const create = useMutation({
    mutationFn: () =>
      prepareDividendRun({
        record_date: recordDate,
        meeting_or_resolution: meetingOrResolution || null,
        pool_kobo: Math.round((Number(poolNaira) || 0) * 100),
        period_key: selectedPeriodKey || null,
        financial_year: selectedPeriodKey ? null : Number(new Date().getFullYear()),
      }),
    onSuccess: async (run) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dividend-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["notification-summary"] }),
      ]);
      navigate(`/dividends/${run.run_id}`);
    },
  });

  const poolValid = Number(poolNaira) > 0;
  const periodValid = selectedPeriodKey.length > 0;
  const recordDateValid =
    recordDate.length > 0 &&
    (!selectedPeriodKey || recordDateInPeriod(recordDate, selectedPeriodKey)) &&
    recordDate <= new Date().toISOString().slice(0, 10);
  const submit = () => {
    if (create.isPending || !recordDateValid || !poolValid || !periodValid) return;
    create.mutate();
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Dividends" breadcrumb="Members / Dividends" />
      {canPrepare && (
        <Card>
          <h2 className="text-lg font-semibold">Prepare a dividend run</h2>
          {effectivePolicyQuery.isError && (
            <p role="alert" className="mt-2 text-sm text-danger">
              Dividend periods could not be loaded. Please try again.
            </p>
          )}
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              Record date
              <input
                type="date"
                className="mt-1 w-full rounded-control border border-border bg-white p-2"
                value={recordDate}
                max={maxRecordDate || new Date().toISOString().slice(0, 10)}
                onChange={(event) => setRecordDate(event.target.value)}
              />
            </label>
            <label className="text-sm">
              Period
              <select
                className="mt-1 w-full rounded-control border border-border bg-white p-2"
                value={selectedPeriodKey}
                onChange={(event) => {
                  const key = event.target.value;
                  setSelectedPeriodKey(key);
                  // Auto-fill record date with period end or today
                  if (key) {
                    const today = new Date().toISOString().slice(0, 10);
                    try {
                      const { end } = periodBoundaries(key);
                      const endStr = end.toISOString().slice(0, 10);
                      setRecordDate(endStr < today ? endStr : today);
                    } catch {
                      setRecordDate(today);
                    }
                  }
                }}
              >
                <option value="">— choose a period —</option>
                {frequency && availablePeriods.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Pool (naira)
              <input
                type="number"
                min="0"
                step="0.01"
                className="mt-1 w-full rounded-control border border-border p-2"
                value={poolNaira}
                onChange={(event) => setPoolNaira(event.target.value)}
              />
            </label>
            <label className="text-sm">
              Meeting or resolution (optional)
              <input
                type="text"
                className="mt-1 w-full rounded-control border border-border p-2"
                value={meetingOrResolution}
                onChange={(event) => setMeetingOrResolution(event.target.value)}
                placeholder="e.g. AGM 2026"
              />
            </label>
          </div>
          <Button
            className="mt-4"
            disabled={create.isPending || !recordDateValid || !poolValid || !periodValid}
            onClick={submit}
          >
            Prepare run
          </Button>
          {create.isError && (
            <p role="alert" className="mt-2 text-sm text-danger">
              {create.error instanceof Error
                ? create.error.message
                : "The run could not be prepared."}
            </p>
          )}
        </Card>
      )}
      <h2 className="text-lg font-semibold">
        {actionNeeded ? "Runs needing approval" : "Dividend runs"}
      </h2>
      {runs.isPending && <Skeleton className="h-48 w-full" />}
      {runs.isError && (
        <EmptyState
          title="Dividend runs could not be loaded"
          detail={runs.error instanceof Error ? runs.error.message : "Please try again."}
          action={<Button onClick={() => void runs.refetch()}>Try again</Button>}
        />
      )}
      {runs.data && items.length === 0 && (
        <EmptyState
          title={actionNeeded ? "No runs need your approval" : "No dividend runs"}
          detail="There are no dividend runs in this view."
        />
      )}
      {runs.data && items.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="p-3">Status</th>
                  <th className="p-3">Year</th>
                  <th className="p-3">Pool</th>
                  <th className="p-3">Record date</th>
                </tr>
              </thead>
              <tbody>
                {items.map((run) => (
                  <tr
                    key={run.run_id}
                    className="cursor-pointer border-b border-border hover:bg-bg"
                    title="View run"
                    onClick={() => navigate(`/dividends/${run.run_id}`)}
                  >
                    <td className="p-3">
                      <Badge>{statusLabel(run.status)}</Badge>
                    </td>
                    <td className="p-3">{run.period_key ?? run.record_date.slice(0, 4)}</td>
                    <td className="p-3">
                      <Money kobo={run.pool_kobo} />
                    </td>
                    <td className="p-3">{formatDate(run.record_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-border p-3">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((value) => value - 1)}
            >
              Previous
            </Button>
            <p className="text-sm text-muted">
              Page {page} of {totalPages}
            </p>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              Next
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

function DividendRunDetail({
  runId,
  roles,
  userId,
}: {
  runId: string;
  roles: string[];
  userId?: string;
}) {
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: ["dividend-run", runId],
    queryFn: () => getDividendRun(runId),
  });
  const members = useQuery({
    queryKey: ["members", "dividends"],
    queryFn: () => getMembers(),
  });
  const memberMap = useMemo(() => {
    const map = new Map<string, { full_name: string }>();
    for (const member of members.data ?? []) map.set(member.id, { full_name: member.full_name });
    return map;
  }, [members.data]);

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["dividend-run", runId] }),
      queryClient.invalidateQueries({ queryKey: ["dividend-runs"] }),
      queryClient.invalidateQueries({ queryKey: ["notification-summary"] }),
    ]);

  const approve = useMutation({
    mutationFn: () => approveDividendRun(runId),
    onSuccess: invalidate,
  });
  const reject = useMutation({
    mutationFn: (reason: string) => rejectDividendRun(runId, reason),
    onSuccess: invalidate,
  });
  const cancel = useMutation({ mutationFn: () => cancelDividendRun(runId), onSuccess: invalidate });

  const data = run.data;
  const onApprove = () => {
    if (approve.isPending) return;
    if (!window.confirm("Approve this dividend run for posting?")) return;
    approve.mutate();
  };
  const onReject = () => {
    if (reject.isPending) return;
    const reason = window.prompt("Reason for rejecting this dividend run:")?.trim() ?? "";
    if (!reason) return;
    reject.mutate(reason);
  };
  const onCancel = () => {
    if (cancel.isPending) return;
    if (!window.confirm("Cancel this dividend run?")) return;
    cancel.mutate();
  };

  if (run.isPending) {
    return (
      <div className="space-y-5">
        <PageHeader title="Dividends" breadcrumb="Members / Dividends" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (run.isError || !data) {
    return (
      <div className="space-y-5">
        <PageHeader title="Dividends" breadcrumb="Members / Dividends" />
        <EmptyState
          title="Dividend run could not be loaded"
          detail={run.error instanceof Error ? run.error.message : "Please try again."}
          action={<Button onClick={() => void run.refetch()}>Try again</Button>}
        />
        <Link to="/dividends" className="text-primary hover:underline">
          Back to dividend runs
        </Link>
      </div>
    );
  }

  const lines = data.items ?? [];
  const totalShares = lines.reduce((sum, line) => sum + line.shares_held, 0);
  const totalDividend = lines.reduce((sum, line) => sum + line.amount_kobo, 0);
  // shares_held is now a share COUNT; compute naira per share
  const perShareRate = totalShares > 0 ? (data.pool_kobo / totalShares / 100).toFixed(4) : null;

  const preparedByMe = data.prepared_by === userId;
  const currentUserId = userId ?? "";
  const canApprove = canApproveDividendRun(roles, data.status, data.prepared_by, currentUserId);
  const canReject = canRejectDividendRun(roles, data.status, data.prepared_by, currentUserId);
  const canCancel = canCancelDividendRun(roles, data.status, data.prepared_by, currentUserId);
  const preparedByLabel = preparedByMe
    ? "you"
    : memberDisplayLabel(memberMap.get(data.prepared_by));

  return (
    <div className="space-y-5">
      <PageHeader title="Dividends" breadcrumb="Members / Dividends" />
      <div>
        <Link to="/dividends" className="text-primary hover:underline">
          Back to dividend runs
        </Link>
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <Badge>{statusLabel(data.status)}</Badge>
          {data.period_key && <span className="text-sm font-medium">{data.period_key}</span>}
          <span className="text-sm text-muted">Record date: {formatDate(data.record_date)}</span>
          <span className="text-sm text-muted">
            Pool: <Money kobo={data.pool_kobo} />
          </span>
          <span className="text-sm text-muted">Prepared by: {preparedByLabel}</span>
          <span className="text-sm text-muted">
            <DateTime value={data.prepared_at} />
          </span>
        </div>
        {data.meeting_or_resolution && (
          <p className="mt-2 text-sm text-muted">
            Meeting / resolution: {data.meeting_or_resolution}
          </p>
        )}
        {data.rejection_reason && (
          <p className="mt-3 text-sm text-danger">Rejection reason: {data.rejection_reason}</p>
        )}
        {data.status === "unprocessed" && (canApprove || canReject || canCancel) && (
          <div className="mt-4 flex flex-wrap gap-2">
            {canApprove && (
              <Button onClick={onApprove} loading={approve.isPending} disabled={approve.isPending}>
                Approve
              </Button>
            )}
            {canReject && (
              <Button
                variant="secondary"
                onClick={onReject}
                loading={reject.isPending}
                disabled={reject.isPending}
              >
                Reject
              </Button>
            )}
            {canCancel && (
              <Button
                variant="secondary"
                onClick={onCancel}
                loading={cancel.isPending}
                disabled={cancel.isPending}
              >
                Cancel
              </Button>
            )}
          </div>
        )}
        {data.status === "unprocessed" && preparedByMe && roles.includes("admin") && (
          <p className="mt-3 text-sm text-muted">
            You prepared this run; another admin must approve it.
          </p>
        )}
      </Card>
      {members.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : lines.length === 0 ? (
        <EmptyState title="No allocations" detail="This run has no allocated dividend lines." />
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="p-3">Member</th>
                  <th className="p-3">Shares</th>
                  <th className="p-3">Dividend</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.line_id} className="border-b border-border">
                    <td className="p-3">{memberDisplayLabel(memberMap.get(line.member_id))}</td>
                    <td className="p-3">{numberFormat.format(line.shares_held)}</td>
                    <td className="p-3">
                      <Money kobo={line.amount_kobo} />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="p-3">Total</td>
                  <td className="p-3">{numberFormat.format(totalShares)}</td>
                  <td className="p-3">
                    <Money kobo={totalDividend} />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="mt-3 text-sm text-muted">
            Per-share rate: {perShareRate !== null ? `₦${perShareRate} per share` : "\u2014"}
          </p>
        </Card>
      )}
    </div>
  );
}
