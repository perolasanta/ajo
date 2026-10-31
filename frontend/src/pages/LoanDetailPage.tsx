import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { useState } from "react";
import { ApiError, decideLoanApplication, getLoanApplication } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { Table, TableCell, TableHead } from "../ui/Table";
import type { LoanApplicationDetail } from "../api/schema";
import { canDecideLoans, canSeeDestinationAccount } from "../loanPolicy";

type Session = { roles: string[] };

export default function LoanDetailPage() {
  const { applicationId } = useParams();
  const detail = useQuery({
    queryKey: ["loan-application", applicationId],
    queryFn: () => getLoanApplication(applicationId ?? ""),
    enabled: Boolean(applicationId),
  });
  const session = useQuery({ queryKey: ["session"], queryFn: () => requestSession() });
  if (detail.isPending) return <DetailSkeleton />;
  if (detail.isError || !detail.data)
    return (
      <EmptyState
        title="Loan application could not be loaded"
        detail={detail.error instanceof ApiError ? detail.error.message : "Please try again."}
        action={<Button onClick={() => void detail.refetch()}>Try again</Button>}
      />
    );
  return <LoanDetail detail={detail.data} roles={session.data?.roles ?? []} />;
}

function LoanDetail({ detail, roles }: { detail: LoanApplicationDetail; roles: string[] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [decision, setDecision] = useState<"approved" | "rejected">("approved");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canDecide = canDecideLoans(roles, detail.status);
  const mutation = useMutation({
    mutationFn: () => decideLoanApplication(detail.id, { decision, reason: reason.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["loan-application", detail.id] });
      queryClient.invalidateQueries({ queryKey: ["loan-applications"] });
      setConfirming(false);
    },
    onError: (value) =>
      setError(value instanceof ApiError ? value.message : "The decision could not be submitted."),
  });
  const submitDecision = () => {
    if (!reason.trim()) return;
    setError(null);
    mutation.mutate();
  };

  return (
    <>
      <PageHeader title={detail.full_name} breadcrumb={`Loans / ${detail.member_no}`}>
        <Button variant="secondary" onClick={() => navigate("/loans")}>
          Back to loans
        </Button>
      </PageHeader>
      <p className="-mt-4 mb-6 text-sm text-muted">
        {detail.member_no} · {detail.product_name}
      </p>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.7fr)]">
        <div className="space-y-5">
          <Card>
            <h2 className="font-serif text-lg font-semibold">Application terms</h2>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2">
              <Item label="Principal" value={<Money kobo={detail.principal_kobo} />} />
              <Item label="Tenor" value={`${detail.tenor_months} months`} />
              <Item label="Annual rate" value={`${(detail.annual_rate_bps / 100).toFixed(2)}%`} />
              <Item label="Status" value={<Badge>{detail.status}</Badge>} />
              <Item label="Submitted" value={<DateTime value={detail.submitted_at} />} />
              <Item label="Decision" value={detail.decision ?? "Pending"} />
              {detail.decision_reason && (
                <Item label="Decision reason" value={detail.decision_reason} />
              )}
            </dl>
            {detail.terms_snapshot && (
              <pre className="mt-5 overflow-x-auto rounded-control bg-bg p-3 text-xs text-muted">
                {JSON.stringify(detail.terms_snapshot, null, 2)}
              </pre>
            )}
          </Card>
          <Card>
            <h2 className="font-serif text-lg font-semibold">Status timeline</h2>
            {detail.status_timeline.length ? (
              <ol className="mt-4 space-y-4 border-l border-border pl-5">
                {detail.status_timeline.map((event, index) => (
                  <li
                    key={`${String(event.status)}-${String(event.at)}-${index}`}
                    className="relative"
                  >
                    <span className="absolute -left-[25px] top-1 size-3 rounded-full border-2 border-surface bg-primary" />
                    <p className="font-medium">{String(event.status)}</p>
                    <p className="mt-1 text-xs text-muted">
                      {typeof event.at === "string" ? (
                        <DateTime value={event.at} />
                      ) : (
                        "Time unavailable"
                      )}
                    </p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-3 text-sm text-muted">
                No status history is available from the API.
              </p>
            )}
          </Card>
          <Card>
            <h2 className="font-serif text-lg font-semibold">Repayment schedule</h2>
            {detail.schedule.length ? (
              <div className="mt-4 overflow-x-auto">
                <Table>
                  <TableHead>
                    <tr>
                      <th>#</th>
                      <th>Due</th>
                      <th>Principal</th>
                      <th>Interest</th>
                      <th>Payment</th>
                      <th>Balance</th>
                      <th>Dues</th>
                      <th>Savings</th>
                    </tr>
                  </TableHead>
                  <tbody>
                    {detail.schedule.map((item) => (
                      <tr key={item.installment_no}>
                        <TableCell>{item.installment_no}</TableCell>
                        <TableCell>
                          <DateTime value={item.due_at} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.principal_kobo} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.interest_kobo} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.payment_kobo} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.balance_kobo} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.dues_kobo} />
                        </TableCell>
                        <TableCell numeric>
                          <Money kobo={item.savings_kobo} />
                        </TableCell>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            ) : (
              <p className="mt-3 text-sm text-muted">No repayment schedule exists yet.</p>
            )}
          </Card>
        </div>
        <div className="space-y-5">
          <Card>
            <h2 className="font-serif text-lg font-semibold">Consents</h2>
            <ul className="mt-4 space-y-3 text-sm">
              {detail.consent_statuses.map((consent) => (
                <li key={consent.kind} className="flex items-center justify-between gap-3">
                  <span>{consent.kind.replaceAll("_", " ")}</span>
                  <Badge>{consent.status}</Badge>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h2 className="font-serif text-lg font-semibold">Guarantors</h2>
            {detail.guarantors.length ? (
              <ul className="mt-4 space-y-3 text-sm">
                {detail.guarantors.map((guarantor) => (
                  <li key={guarantor.member_no}>
                    <span className="font-medium">{guarantor.full_name}</span>
                    <span className="ml-2 text-muted">{guarantor.member_no}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-muted">No guarantors recorded.</p>
            )}
          </Card>
          {canSeeDestinationAccount(roles) && detail.destination_bank_account && (
            <Card>
              <h2 className="font-serif text-lg font-semibold">Destination account</h2>
              <p className="mt-3 text-sm">
                {detail.destination_bank_account.bank_code} · ••••{" "}
                {detail.destination_bank_account.last4}
              </p>
            </Card>
          )}
          {canDecide && (
            <Card>
              <h2 className="font-serif text-lg font-semibold">Committee decision</h2>
              {error && (
                <p
                  className="mt-3 rounded-control border border-danger/30 bg-danger/5 p-3 text-sm text-danger"
                  role="alert"
                >
                  {error}
                </p>
              )}
              {confirming ? (
                <div className="mt-4 space-y-3">
                  <p className="text-sm">
                    Submit <strong>{decision}</strong> for this application?
                  </p>
                  <div className="flex gap-2">
                    <Button loading={mutation.isPending} onClick={submitDecision}>
                      Confirm decision
                    </Button>
                    <Button variant="secondary" onClick={() => setConfirming(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-4 space-y-4">
                  <Select
                    aria-label="Decision"
                    value={decision}
                    onChange={(event) => setDecision(event.target.value as typeof decision)}
                  >
                    <option value="approved">Approve</option>
                    <option value="rejected">Reject</option>
                  </Select>
                  <Input
                    aria-label="Decision reason"
                    placeholder="Required reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                  <Button disabled={!reason.trim()} onClick={() => setConfirming(true)}>
                    Review decision
                  </Button>
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function Item({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 font-medium">{value}</dd>
    </div>
  );
}
function DetailSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-5 lg:grid-cols-2">
        <Skeleton className="h-72" />
        <Skeleton className="h-72" />
      </div>
    </div>
  );
}
async function requestSession(): Promise<Session> {
  const response = await fetch("/api/v1/auth/me", { credentials: "include" });
  if (!response.ok) throw new Error("Session unavailable");
  return response.json() as Promise<Session>;
}
