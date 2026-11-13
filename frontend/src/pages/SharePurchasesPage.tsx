import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getMembers, getNotificationSummary, getSharePurchases, request, requestSharePurchase } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

 type Session = { user_id: string; roles: string[] };

export default function SharePurchasesPage() {
  const client = useQueryClient();
  const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me") });
  const roles = session.data?.roles ?? [];
  const treasurer = roles.includes("treasurer");
  const canRequest = roles.includes("officer") || treasurer;
  const summary = useQuery({ queryKey: ["notification-summary"], queryFn: getNotificationSummary });
  const count = treasurer ? summary.data?.share_purchases?.awaiting_approval ?? 0 : 0;
  const [mine, setMine] = useState<"all" | "needed">("all");
  const [memberId, setMemberId] = useState("");
  const [sharesCount, setSharesCount] = useState("1");
  const [selectedMember, setSelectedMember] = useState<string | undefined>();
  const members = useQuery({ queryKey: ["members", "share-purchase"], queryFn: () => getMembers() });
  const savings = useQuery({
    queryKey: ["member", selectedMember],
    queryFn: () => request<{ balances?: { savings_kobo: number } }>(`/members/${selectedMember}`),
    enabled: Boolean(selectedMember),
  });
  const purchases = useQuery({
    queryKey: ["share-purchases", mine],
    queryFn: () => getSharePurchases({ mine, status: mine === "needed" ? "pending" : undefined }),
  });
  const create = useMutation({
    mutationFn: () => requestSharePurchase({ member_id: memberId, shares_count: Number(sharesCount) }),
    onSuccess: async () => {
      setMemberId("");
      setSelectedMember(undefined);
      setSharesCount("1");
      await client.invalidateQueries({ queryKey: ["share-purchases"] });
    },
  });
  const decision = useMutation({
    mutationFn: ({ id, action, note }: { id: string; action: "approve" | "reject"; note?: string }) =>
      request(`/share-purchases/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action === "reject" ? JSON.stringify({ note }) : "{}",
      }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["share-purchases"] }),
        client.invalidateQueries({ queryKey: ["notification-summary"] }),
      ]);
    },
  });
  const userSelected = useRef(false);
  useEffect(() => {
    if (!userSelected.current && treasurer && count > 0) setMine("needed");
  }, [count, treasurer]);
  const submit = () => {
    if (!memberId || !Number.isInteger(Number(sharesCount)) || Number(sharesCount) <= 0) return;
    create.mutate();
  };
  const decide = (id: string, action: "approve" | "reject") => {
    if (decision.isPending) return;
    const item = purchases.data?.items.find((entry) => entry.id === id);
    if (!item || item.requested_by === session.data?.user_id) return;
    const note = action === "reject" ? window.prompt("Note for rejecting this share purchase:")?.trim() ?? "" : undefined;
    if (action === "reject" && !note) return;
    if (!window.confirm(`${action === "approve" ? "Approve" : "Reject"} ${item.shares_count} shares?`)) return;
    decision.mutate({ id, action, note });
  };
  return <div className="space-y-5">
    <PageHeader title="Share purchases" breadcrumb="Members / Share purchases" />
    {canRequest && <Card><h2 className="text-lg font-semibold">Request shares from savings</h2><div className="mt-4 grid gap-3 md:grid-cols-3">
      <label className="text-sm">Member<select className="mt-1 w-full rounded-control border border-border bg-white p-2" value={memberId} onChange={(event) => { setMemberId(event.target.value); setSelectedMember(event.target.value || undefined); }}><option value="">Select a member</option>{members.data?.map((member) => <option key={member.id} value={member.id}>{member.full_name} ({member.member_no})</option>)}</select></label>
      <label className="text-sm">Shares<input className="mt-1 w-full rounded-control border border-border p-2" type="number" min="1" step="1" value={sharesCount} onChange={(event) => setSharesCount(event.target.value)} /></label>
      <div className="text-sm">Current savings<strong className="mt-1 block text-lg">{savings.data?.balances ? <Money kobo={savings.data.balances.savings_kobo} /> : "Select a member"}</strong></div>
    </div><Button className="mt-4" disabled={create.isPending || !memberId} onClick={submit}>Submit request</Button>{create.isError && <p role="alert" className="mt-2 text-sm text-danger">{create.error instanceof Error ? create.error.message : "The request could not be submitted."}</p>}</Card>}
    <div className="flex gap-2" role="tablist" aria-label="Share purchase views"><Button role="tab" aria-selected={mine === "needed"} variant={mine === "needed" ? "primary" : "secondary"} onClick={() => { userSelected.current = true; setMine("needed"); }}>Needs my action{count ? ` (${count})` : ""}</Button><Button role="tab" aria-selected={mine === "all"} variant={mine === "all" ? "primary" : "secondary"} onClick={() => { userSelected.current = true; setMine("all"); }}>All</Button></div>
    {purchases.isPending && <Skeleton className="h-48 w-full" />}
    {purchases.isError && <EmptyState title="Share purchases could not be loaded" detail={purchases.error instanceof Error ? purchases.error.message : "Please try again."} />}
    {purchases.data?.items.length === 0 && <EmptyState title={mine === "needed" ? "No items need your action" : "No share purchases"} detail="There are no requests in this view." />}
    {purchases.data && purchases.data.items.length > 0 && <Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Member</th><th className="p-3">Shares</th><th className="p-3">Amount</th><th className="p-3">Requester</th><th className="p-3">Requested</th><th className="p-3">Status / action</th></tr></thead><tbody>{purchases.data.items.map((item) => { const requester = item.requested_by === session.data?.user_id; return <tr key={item.id} className="border-b border-border"><td className="p-3">{item.member_name}</td><td className="p-3">{item.shares_count}</td><td className="p-3"><Money kobo={item.amount_kobo} /></td><td className="p-3">{item.requester_name}</td><td className="p-3"><DateTime value={item.created_at} /></td><td className="p-3">{item.status !== "pending" ? <Badge>{item.status}</Badge> : requester ? "You requested this; another treasurer must decide" : treasurer ? <div className="flex gap-2"><Button size="sm" disabled={decision.isPending} onClick={() => decide(item.id, "approve")}>Approve</Button><Button size="sm" variant="secondary" disabled={decision.isPending} onClick={() => decide(item.id, "reject")}>Reject</Button></div> : "Treasurer approval required"}</td></tr>; })}</tbody></table></div></Card>}
  </div>;
}
