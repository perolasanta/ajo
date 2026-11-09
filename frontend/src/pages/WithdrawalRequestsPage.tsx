import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getNotificationSummary, getWithdrawalRequests, request } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

type Session = { user_id: string; roles: string[] };

export default function WithdrawalRequestsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [mine, setMine] = useState<"all" | "needed">(() => searchParams.get("mine") === "needed" ? "needed" : "all");
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const userSelectedTab = useRef(false);
  const client = useQueryClient();
  const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me") });
  const roles = session.data?.roles ?? [];
  const treasurer = roles.includes("treasurer");
  const officer = roles.includes("officer") && !treasurer && !roles.includes("admin");
  const summary = useQuery({ queryKey: ["notification-summary"], queryFn: getNotificationSummary });
  const count = treasurer ? summary.data?.withdrawal_requests?.awaiting_approval ?? 0 : 0;
  useEffect(() => {
    if (!userSelectedTab.current && treasurer && count > 0 && !searchParams.has("mine")) {
      const next = new URLSearchParams(searchParams);
      next.set("mine", "needed");
      setSearchParams(next, { replace: true });
      setMine("needed");
    }
  }, [count, searchParams, setSearchParams, treasurer]);
  const requests = useQuery({
    queryKey: ["withdrawal-request-queue", mine, page],
    queryFn: () => getWithdrawalRequests({ status: mine === "needed" ? "pending" : undefined, mine, page, pageSize }),
  });
  const action = useMutation({
    mutationFn: async ({ id, decision, reason }: { id: string; decision: "approve" | "reject"; reason?: string }) =>
      request(`/members/withdrawals/${id}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: decision === "reject" ? JSON.stringify({ reason }) : "{}",
      }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["withdrawal-request-queue"] }),
        client.invalidateQueries({ queryKey: ["notification-summary"] }),
      ]);
    },
  });
  const focusedId = searchParams.get("request");
  function selectTab(tab: "all" | "needed") {
    userSelectedTab.current = true;
    setMine(tab);
    setPage(1);
    const next = new URLSearchParams(searchParams);
    if (tab === "needed") next.set("mine", "needed");
    else next.delete("mine");
    setSearchParams(next);
  }
  function resolve(item: NonNullable<typeof requests.data>['items'][number], decision: "approve" | "reject") {
    if (item.requested_by === session.data?.user_id || action.isPending) return;
    const reason = decision === "reject" ? window.prompt("Reason for rejecting this withdrawal:")?.trim() ?? "" : undefined;
    if (decision === "reject" && !reason) return;
    if (!window.confirm(`${decision === "approve" ? "Approve" : "Reject"} ₦${(item.amount_kobo / 100).toFixed(2)} for ${item.member_name}?`)) return;
    action.mutate({ id: item.id, decision, reason });
  }
  return <div className="space-y-5">
    <PageHeader title="Withdrawal requests" breadcrumb="Members / Requests" />
    {officer && <p className="rounded-control bg-amber-50 p-3 text-sm text-amber-950">Officers can review pending requests; only a treasurer may approve or reject them.</p>}
    <div className="flex gap-2" role="tablist" aria-label="Withdrawal request views">
      <Button variant={mine === "needed" ? "primary" : "secondary"} role="tab" aria-selected={mine === "needed"} onClick={() => selectTab("needed")}>Needs my action{count > 0 ? ` (${count})` : ""}</Button>
      <Button variant={mine === "all" ? "primary" : "secondary"} role="tab" aria-selected={mine === "all"} onClick={() => selectTab("all")}>All</Button>
    </div>
    {action.isError && <p role="alert" className="text-sm text-danger">{action.error instanceof Error ? action.error.message : "The withdrawal action could not be completed."}</p>}
    {requests.isPending && <Skeleton className="h-48 w-full" />}
    {requests.isError && <EmptyState title="Withdrawal requests could not be loaded" detail={requests.error instanceof Error ? requests.error.message : "Please try again."} action={<Button onClick={() => void requests.refetch()}>Try again</Button>} />}
    {requests.data && requests.data.items.length === 0 && <EmptyState title={mine === "needed" ? "No items need your action" : "No withdrawal requests"} detail={mine === "needed" && !treasurer ? "Only treasurers can approve or reject withdrawal requests." : "There are no requests in this view."} />}
    {requests.data && requests.data.items.length > 0 && <Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Member</th><th className="p-3">Amount</th><th className="p-3">Requester</th><th className="p-3">Requested</th><th className="p-3">Status</th><th className="p-3">Action</th></tr></thead><tbody>{requests.data.items.map((item) => {
      const isRequester = item.requested_by === session.data?.user_id;
      return <tr key={item.id} id={`withdrawal-${item.id}`} className={`border-b border-border ${focusedId === item.id ? "bg-amber-50 ring-2 ring-inset ring-primary/30" : mine === "needed" ? "bg-amber-50/60" : ""}`}><td className="p-3"><Link className="font-medium text-primary underline" to={`/members/${item.member_id}?withdrawal_request=${item.id}`}>{item.member_name}<span className="block text-xs text-muted">{item.member_no}</span></Link></td><td className="p-3"><Money kobo={item.amount_kobo} /></td><td className="p-3">{item.requester_name}</td><td className="p-3"><DateTime value={item.requested_at} /></td><td className="p-3"><Badge>{item.status}</Badge></td><td className="p-3">{item.status !== "pending" ? item.rejection_reason ? `Rejected: ${item.rejection_reason}` : "—" : isRequester ? "You requested this; another treasurer must approve" : !treasurer ? "Treasurer approval required" : <div className="flex gap-2"><Button size="sm" disabled={action.isPending} onClick={() => resolve(item, "approve")}>Approve</Button><Button size="sm" variant="secondary" disabled={action.isPending} onClick={() => resolve(item, "reject")}>Reject</Button></div>}</td></tr>;
    })}</tbody></table></div><div className="flex items-center justify-between gap-3 p-3 text-xs text-muted"><span>{requests.data.total} requests · Page {page} of {Math.max(1, Math.ceil(requests.data.total / pageSize))}</span><div className="flex gap-2"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</Button><Button size="sm" variant="secondary" disabled={page >= Math.ceil(requests.data.total / pageSize)} onClick={() => setPage((value) => value + 1)}>Next</Button></div></div></Card>}
  </div>;
}
