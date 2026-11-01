import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getPayrollRun, ApiError, request } from "../api/client";
import { payrollRunActions, payrollStepIndex, payrollSteps } from "../payrollPolicy.mjs";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton, Stepper } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type Session = { user_id: string; roles: string[] };

function DetailError({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  return (
    <EmptyState
      title="Payroll run could not be loaded"
      detail={`${message}${error instanceof ApiError && error.requestId ? ` Request ID: ${error.requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

export default function PayrollDetailPage() {
  const { runId } = useParams();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const run = useQuery({
    queryKey: ["payroll-run", runId],
    queryFn: () => getPayrollRun(runId ?? ""),
    enabled: Boolean(runId),
  });

  if (run.isPending || session.isPending) {
    return (
      <div className="space-y-4" aria-label="Loading payroll run">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (run.isError) return <DetailError error={run.error} retry={() => void run.refetch()} />;
  if (session.isError || !session.data) {
    return <EmptyState title="Session unavailable" detail="Sign in again to continue." />;
  }
  const data = run.data;
  const actions = payrollRunActions(session.data.roles, session.data.user_id, data);

  return (
    <>
      <PageHeader
        title={`Payroll run ${data.period_year}-${String(data.period_month).padStart(2, "0")}`}
        breadcrumb="Monthly deduction run"
      />
      <Link className="mb-4 inline-block text-sm text-primary hover:underline" to="/payroll">
        Back to payroll runs
      </Link>
      <div className="mb-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted">Run status</p>
          <Badge status={data.status} />
        </div>
        <div className="mt-5 overflow-hidden">
          <Stepper steps={payrollSteps} current={payrollStepIndex(data.status)} />
        </div>
      </div>

      <section className="rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Reconciliation summary</h2>
        <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">Expected total</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.expected_total_kobo} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Imported file total</dt>
            <dd className="mt-1 font-medium">
              {data.file_total_kobo === null ? (
                "Not imported"
              ) : (
                <Money kobo={data.file_total_kobo} />
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Bank receipt total</dt>
            <dd className="mt-1 font-medium">
              {data.bank_receipt_total_kobo === null ? (
                "Not recorded"
              ) : (
                <Money kobo={data.bank_receipt_total_kobo} />
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Receipt variance (file − receipt)</dt>
            <dd className="mt-1 font-medium">
              {data.variance_kobo === null ? "Not reconciled" : <Money kobo={data.variance_kobo} />}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Loan component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.loan} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Savings component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.savings} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Dues component</dt>
            <dd className="mt-1 font-medium">
              <Money kobo={data.component_totals_kobo.dues} />
            </dd>
          </div>
        </dl>
        <div className="mt-5 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Row counts</h3>
          <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            {Object.entries(data.counts).map(([classification, count]) => (
              <div key={classification} className="flex justify-between gap-3">
                <dt className="capitalize text-muted">{classification.replaceAll("_", " ")}</dt>
                <dd className="font-medium tabular-nums">{count}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="mt-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Persisted exceptions</h2>
        <p className="mt-1 text-sm text-muted">
          Only imported exception fields are stored and shown. Original file row numbers are not
          retained.
        </p>
        {data.exceptions.length === 0 ? (
          <p className="mt-4 text-sm text-muted">No persisted payroll exceptions.</p>
        ) : (
          <div className="mt-4 space-y-3">
            {data.exceptions.map((exception, index) => (
              <article
                key={`${exception.staff_no_raw}-${exception.classification}-${index}`}
                className="rounded-control border border-border p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="break-all font-medium">{exception.staff_no_raw}</p>
                  <Badge status={exception.classification} />
                </div>
                <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted">Actual amount</dt>
                    <dd>
                      {exception.actual_kobo === null ? (
                        "—"
                      ) : (
                        <Money kobo={exception.actual_kobo} />
                      )}
                    </dd>
                  </div>
                  {exception.resolution_note && (
                    <div className="sm:col-span-2">
                      <dt className="text-muted">Resolution note</dt>
                      <dd className="mt-1 break-words">{exception.resolution_note}</dd>
                    </div>
                  )}
                </dl>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="mt-6 rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Available actions</h2>
        {actions.canExportExpected || actions.canDryRun ? (
          <p className="mt-1 text-sm text-muted">
            Officer and treasurer file actions are available while this run is unimported.
          </p>
        ) : null}
        {actions.canPost ? (
          <p className="mt-1 text-sm text-muted">A treasurer may post this imported run.</p>
        ) : null}
        {actions.canSignOff ? (
          <p className="mt-1 text-sm text-muted">
            An administrator who did not import or post this run may sign it off.
          </p>
        ) : null}
        {actions.signOffBlockedBySelf && (
          <p className="mt-1 text-sm text-muted">
            A different administrator must sign off because you imported or posted this run.
          </p>
        )}
        {actions.signOffBlockedMissingPoster && (
          <p className="mt-1 text-sm text-muted">
            Sign-off is unavailable because the original importer or poster is not recorded.
          </p>
        )}
        {!actions.canExportExpected &&
          !actions.canPost &&
          !actions.canSignOff &&
          !actions.signOffBlockedBySelf &&
          !actions.signOffBlockedMissingPoster && (
            <p className="mt-1 text-sm text-muted">
              No actions are available for your role in this run state.
            </p>
          )}
      </section>

      <section className="mt-6 rounded-card border border-border bg-surface p-4 text-sm text-muted sm:p-5">
        <h2 className="font-serif text-lg font-semibold text-ink">Run history</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <dt>Created by</dt>
            <dd className="mt-1 break-all text-ink">{data.created_by}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd className="mt-1 text-ink">
              <DateTime value={data.created_at} />
            </dd>
          </div>
          {data.imported_by && (
            <div>
              <dt>Imported by</dt>
              <dd className="mt-1 break-all text-ink">{data.imported_by}</dd>
            </div>
          )}
          {data.imported_at && (
            <div>
              <dt>Imported</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.imported_at} />
              </dd>
            </div>
          )}
          {data.posted_by && (
            <div>
              <dt>Posted by</dt>
              <dd className="mt-1 break-all text-ink">{data.posted_by}</dd>
            </div>
          )}
          {data.posted_at && (
            <div>
              <dt>Posted</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.posted_at} />
              </dd>
            </div>
          )}
          {data.signed_off_at && (
            <div>
              <dt>Signed off</dt>
              <dd className="mt-1 text-ink">
                <DateTime value={data.signed_off_at} />
              </dd>
            </div>
          )}
        </dl>
      </section>
    </>
  );
}
