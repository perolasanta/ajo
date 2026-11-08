import * as Tabs from "@radix-ui/react-tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getMember, getMemberActivity, getMemberLoans, getMemberStatement, request } from "../api/client";
import { canSeeMemberFinancialActions } from "../memberFinancialPolicy.js";
import type { MemberActivityPage, MemberLoanPage, MemberStatementPage } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateReceived, DateTime, Money, RecordedAt } from "../ui/Format";
import { completeAttempt, createAttemptKeyState, getAttemptKey } from "../memberActionForm.mjs";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";

type Section = "overview" | "statement" | "loans" | "activity" | "actions" | "channels";
type Session = { roles: string[] };
const sections: Array<[Section, string]> = [["overview", "Overview"], ["statement", "Statement"], ["loans", "Loans"], ["activity", "Activity"], ["actions", "Actions"], ["channels", "Channels"]];

export default function MemberDetailPage() {
  const { memberId } = useParams();
  const [section, setSection] = useState<Section>("overview");
  const [page, setPage] = useState(1);
  const detail = useQuery({ queryKey: ["member", memberId], queryFn: () => getMember(memberId ?? ""), enabled: Boolean(memberId) });
  const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me") });
  const canAct = canSeeMemberFinancialActions(session.data?.roles ?? []);
  const tabQuery = useQuery<MemberStatementPage | MemberLoanPage | MemberActivityPage>({
    queryKey: ["member-tab", memberId, section, page],
    queryFn: () => {
      if (!memberId) throw new Error("Member ID is required");
      if (section === "statement") return getMemberStatement(memberId, { page });
      if (section === "loans") return getMemberLoans(memberId, { page });
      return getMemberActivity(memberId, { page });
    },
    enabled: Boolean(memberId) && ["statement", "loans", "activity"].includes(section),
  });
  if (detail.isPending) return <MemberDetailSkeleton />;
  if (detail.isError || !detail.data) return <MemberError error={detail.error} retry={() => void detail.refetch()} />;
  const member = detail.data;
  const changeSection = (value: Section) => { setSection(value); setPage(1); };
  return <>
    <PageHeader title={member.full_name} breadcrumb={`Members / ${member.full_name}`}>{member.status && <Badge>{member.status}</Badge>}</PageHeader>
    <p className="-mt-4 mb-6 text-sm text-muted">{member.member_no}{member.joined_on && <> · Joined <DateTime value={member.joined_on} /></>}</p>
    <Tabs.Root value={section} onValueChange={(value) => changeSection(value as Section)}>
      <div className="mb-5 sm:hidden"><label className="mb-1 block text-sm font-medium" htmlFor="member-section">Section</label><Select id="member-section" value={section} onChange={(event) => changeSection(event.target.value as Section)}>{sections.filter(([value]) => value !== "actions" || canAct).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></div>
      <Tabs.List className="mb-5 hidden flex-wrap gap-1 border-b border-border sm:flex" aria-label="Member record sections">{sections.filter(([value]) => value !== "actions" || canAct).map(([value, label]) => <Tabs.Trigger key={value} value={value} className="border-b-2 border-transparent px-3 py-2 text-sm text-muted data-[state=active]:border-primary data-[state=active]:font-medium data-[state=active]:text-ink">{label}</Tabs.Trigger>)}</Tabs.List>
      <Tabs.Content value="overview"><Overview member={member} /></Tabs.Content>
      <Tabs.Content value="statement"><Statement query={tabQuery.data as MemberStatementPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      <Tabs.Content value="loans"><Loans query={tabQuery.data as MemberLoanPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      <Tabs.Content value="activity"><Activity query={tabQuery.data as MemberActivityPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      {canAct && <Tabs.Content value="actions"><Actions memberId={member.id} memberName={member.full_name} availableKobo={member.balances?.available_kobo ?? 0} /></Tabs.Content>}
      <Tabs.Content value="channels"><ComingSoon title="Coming soon" detail="Channel binding is not available yet." /></Tabs.Content>
    </Tabs.Root>
  </>;
}

function Overview({ member }: { member: Awaited<ReturnType<typeof getMember>> }) {
  const balances = member.balances;
  const hasContactDetails = member.phone_e164 !== undefined || member.email !== undefined || member.staff_no !== undefined || member.employer !== undefined;
  return <div className="space-y-5"><section className="grid items-start gap-4 sm:grid-cols-2 lg:grid-cols-4">
    <Card className="p-4"><p className="text-sm text-muted">Member number</p><p className="mt-2 font-semibold">{member.member_no}</p></Card>
    <Card className="p-4"><p className="text-sm text-muted">Joined</p><p className="mt-2 font-semibold">{member.joined_on ? <DateTime value={member.joined_on} /> : "—"}</p></Card>
    <Card className="p-4"><p className="text-sm text-muted">Savings balance</p><p className="mt-2 font-semibold">{balances ? <Money kobo={balances.savings_kobo} /> : "—"}</p></Card>
    <Card className="p-4"><p className="text-sm text-muted">Active loans</p><p className="mt-2 font-semibold">{balances?.active_loans_count ?? "—"}</p></Card>
    <Card className="p-4"><p className="text-sm text-muted">Savings locked as loan backing</p><p className="mt-2 font-semibold">{balances ? <Money kobo={balances.locked_kobo} /> : "—"}</p></Card>
    <Card className="p-4"><p className="text-sm text-muted">Available to withdraw</p><p className="mt-2 font-semibold">{balances ? <Money kobo={balances.available_kobo} /> : "—"}</p></Card>
  </section>{hasContactDetails && <Card><h2 className="font-serif text-lg font-semibold">Contact details</h2><dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2"><Item label="Phone" value={member.phone_e164 ?? "—"} /><Item label="Email" value={member.email ?? "—"} /><Item label="Staff no." value={member.staff_no ?? "—"} /><Item label="Employer" value={member.employer ?? "—"} /></dl></Card>}{member.bank_accounts !== undefined && <Card><h2 className="font-serif text-lg font-semibold">Bank accounts</h2>{member.bank_accounts.length ? <ul className="mt-4 space-y-3 text-sm">{member.bank_accounts.map((account) => <li key={account.id}><p className="font-medium">Bank: {account.bank_code}</p><p className="text-muted">•••• {account.last4}</p>{account.account_name && <p className="text-muted">{account.account_name}</p>}</li>)}</ul> : <p className="mt-3 text-sm text-muted">No bank account on file.</p>}</Card>}</div>;
}

type TabProps<T> = { query?: T; isPending: boolean; isError: boolean; page: number; setPage: (page: number) => void };
function Statement({ query, isPending, isError, page, setPage }: TabProps<MemberStatementPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Statement unavailable" detail="The member statement could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Date received</th><th className="p-3">Recorded at</th><th className="p-3">Type</th><th className="p-3">Amount</th><th className="p-3">Running balance</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.id} className="border-b border-border"><td className="p-3"><DateReceived value={item.date} /></td><td className="p-3"><RecordedAt value={item.recorded_at} /></td><td className="p-3 capitalize">{item.type}</td><td className="p-3"><Money kobo={item.amount_kobo} /></td><td className="p-3"><Money kobo={item.running_balance_kobo} /></td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No ledger entries.</p>}</div></Card></Paginated>; }
function Loans({ query, isPending, isError, page, setPage }: TabProps<MemberLoanPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Loans unavailable" detail="The member loans could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Application</th><th className="p-3">Status</th><th className="p-3">Principal</th><th className="p-3">Outstanding</th><th className="p-3">Next instalment</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.application_id} className="border-b border-border"><td className="p-3"><Link className="text-primary underline" to={`/loans/${item.application_id}`}>View loan</Link></td><td className="p-3"><Badge>{item.loan_status ?? item.application_status}</Badge></td><td className="p-3"><Money kobo={item.principal_kobo} /></td><td className="p-3">{item.outstanding_balance_kobo == null ? "—" : <Money kobo={item.outstanding_balance_kobo} />}</td><td className="p-3">{item.next_installment_at ? <><DateTime value={item.next_installment_at} /> · <Money kobo={item.next_installment_amount_kobo ?? 0} /></> : "—"}</td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No loans or applications.</p>}</div></Card></Paginated>; }
function Activity({ query, isPending, isError, page, setPage }: TabProps<MemberActivityPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Activity unavailable" detail="Member activity could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Time</th><th className="p-3">Action</th><th className="p-3">Actor</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.event_id} className="border-b border-border"><td className="p-3"><DateTime value={item.timestamp} /></td><td className="p-3">{item.action}</td><td className="p-3">{item.actor_display_name}</td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No activity entries.</p>}</div></Card></Paginated>; }
function Paginated({ page, setPage, total, pageSize, children }: { page: number; setPage: (page: number) => void; total: number; pageSize: number; children: React.ReactNode }) { const totalPages = Math.max(1, Math.ceil(total / pageSize)); return <div className="space-y-3">{children}<div className="flex items-center justify-between text-sm text-muted"><span>{total} records</span><div className="flex gap-2"><Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><span className="px-2 py-2">Page {page} of {totalPages}</span><Button variant="secondary" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button></div></div></div>; }
function ComingSoon({ title, detail }: { title: string; detail: string }) { return <EmptyState title={title} detail={detail} />; }
type Withdrawal = { id: string; amount_kobo: number; status: string; created_at: string; requested_by: string; requester_name: string; rejection_reason?: string | null };
function Actions({ memberId, memberName, availableKobo }: { memberId: string; memberName: string; availableKobo: number }) {
  const client = useQueryClient();
  const actionLock = useRef(false);
  const attemptKeys = useRef(createAttemptKeyState());
  const requests = useQuery({ queryKey: ["member-withdrawals", memberId], queryFn: () => request<{ items: Withdrawal[]; current_user_id: string }>(`/members/${memberId}/withdrawals`) });
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [success, setSuccess] = useState("");
  const refresh = async () => { await Promise.all([client.invalidateQueries({ queryKey: ["member-withdrawals", memberId] }), client.invalidateQueries({ queryKey: ["member-tab", memberId, "statement"] }), client.invalidateQueries({ queryKey: ["member", memberId] }), client.invalidateQueries({ queryKey: ["notification-summary"] })]); };
  const submit = async (event: React.FormEvent<HTMLFormElement>, kind: "contribution" | "withdrawal") => {
    event.preventDefault();
    const form = event.currentTarget;
    if (busy || actionLock.current) return;
    setError(""); setSuccess("");
    const formData = new FormData(form);
    const amount = Number(formData.get("amount")); const kobo = Math.round(amount * 100);
    if (!Number.isFinite(amount) || amount <= 0 || Math.abs(amount * 100 - kobo) > 0.0001) { setError("Enter a positive amount with no more than two decimal places."); return; }
    if (kind === "withdrawal" && kobo > availableKobo) { setError("Withdrawal exceeds the member’s available amount."); return; }
    actionLock.current = true;
    if (!window.confirm(`${kind === "contribution" ? "Record contribution" : "Request withdrawal"} of ₦${(kobo / 100).toFixed(2)} for ${memberName}?`)) { actionLock.current = false; return; }
    const idempotency_key = getAttemptKey(attemptKeys.current, kind);
    const body = kind === "contribution"
      ? { member_id: memberId, kind: formData.get("kind"), amount_kobo: kobo, effective_date: formData.get("date"), reference: formData.get("reference"), payment_method: formData.get("method"), idempotency_key }
      : { member_id: memberId, amount_kobo: kobo, reason: formData.get("reason"), idempotency_key };
    setBusy(true);
    try {
      await request(kind === "contribution" ? "/members/contributions" : "/members/withdrawals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      completeAttempt(attemptKeys.current, kind);
      form.reset();
      setSuccess(kind === "contribution" ? "Contribution recorded successfully." : "Withdrawal request submitted successfully.");
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "The action failed. Please try again."); }
    finally { actionLock.current = false; setBusy(false); }
  };
  const resolve = async (item: Withdrawal, action: "approve" | "reject") => { if (busy || actionLock.current) return; actionLock.current = true; let reason = ""; if (action === "reject") { reason = window.prompt("Reason for rejecting this withdrawal:")?.trim() ?? ""; if (!reason) { actionLock.current = false; return; } } if (!window.confirm(`${action === "approve" ? "Approve and post" : "Reject"} ₦${(item.amount_kobo / 100).toFixed(2)} for ${memberName}?`)) { actionLock.current = false; return; } setBusy(true); setError(""); try { await request(`/members/withdrawals/${item.id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: action === "reject" ? JSON.stringify({ reason }) : "{}" }); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : "The action failed. Please try again."); } finally { actionLock.current = false; setBusy(false); } };
  return <div className="space-y-5"><p role="alert" className="text-sm text-danger">{error}</p><p role="status" className="text-sm text-green-700">{success}</p><section className="grid gap-4 md:grid-cols-2"><Card><h2 className="font-serif text-lg font-semibold">Record contribution</h2><form className="mt-3 space-y-3" onSubmit={(e) => void submit(e, "contribution")}><label className="block text-sm">Amount (₦)<input name="amount" type="number" min="0.01" step="0.01" required className="mt-1 w-full rounded border p-2" /></label><label className="block text-sm">Contribution type<select name="kind" className="mt-1 w-full rounded border p-2"><option value="savings">Savings</option><option value="shares">Shares</option><option value="dues">Dues</option></select></label><label className="block text-sm">Date received<input name="date" type="date" required defaultValue={new Date().toISOString().slice(0,10)} className="mt-1 w-full rounded border p-2" /></label><label className="block text-sm">Reference / note<input name="reference" maxLength={200} className="mt-1 w-full rounded border p-2" /></label><label className="block text-sm">Payment method<input name="method" required maxLength={40} defaultValue="cash" className="mt-1 w-full rounded border p-2" /></label><Button type="submit" disabled={busy}>{busy ? "Recording…" : "Record contribution"}</Button></form></Card><Card><h2 className="font-serif text-lg font-semibold">Request withdrawal</h2><p className="my-2 text-sm text-muted">Available: <Money kobo={availableKobo} /></p><form className="space-y-3" onSubmit={(e) => void submit(e, "withdrawal")}><label className="block text-sm">Amount (₦)<input name="amount" type="number" min="0.01" step="0.01" max={(availableKobo / 100).toFixed(2)} required className="mt-1 w-full rounded border p-2" /></label><label className="block text-sm">Reason<textarea name="reason" required maxLength={500} className="mt-1 w-full rounded border p-2" /></label><Button type="submit" disabled={busy}>{busy ? "Submitting…" : "Request withdrawal"}</Button></form></Card></section><Card><h2 className="font-serif text-lg font-semibold">Withdrawal requests</h2>{requests.isError ? <p className="mt-3 text-sm text-danger">Could not load withdrawal requests.</p> : <ul className="mt-3 divide-y divide-border">{requests.data?.items.map((item) => <li key={item.id} className="space-y-2 py-3"><p><Badge>{item.status}</Badge> · <Money kobo={item.amount_kobo} /> · <span>Recorded at <RecordedAt value={item.created_at} /></span></p><p className="text-sm">Requested by {item.requester_name}</p>{item.status === "rejected" && item.rejection_reason && <p className="text-sm text-danger">Reason: {item.rejection_reason}</p>}{item.status === "pending" && item.requested_by === requests.data.current_user_id && <p className="text-sm text-muted">You requested this; another treasurer must approve</p>}{item.status === "pending" && <div className="flex gap-2"><Button disabled={busy || item.requested_by === requests.data?.current_user_id} onClick={() => void resolve(item, "approve")}>Approve</Button><Button variant="secondary" disabled={busy || item.requested_by === requests.data?.current_user_id} onClick={() => void resolve(item, "reject")}>Reject</Button></div>}</li>)}{requests.data?.items.length === 0 && <li className="py-3 text-sm text-muted">No withdrawal requests.</li>}</ul>}</Card></div>;
}
function Item({ label, value }: { label: string; value: React.ReactNode }) { return <div><dt className="text-xs uppercase tracking-wide text-muted">{label}</dt><dd className="mt-1 font-medium">{value}</dd></div>; }
function TabSkeleton() { return <div className="space-y-3"><Skeleton className="h-40 w-full" /><Skeleton className="h-8 w-40" /></div>; }
function MemberError({ error, retry }: { error: unknown; retry: () => void }) { const requestId = error instanceof ApiError ? error.requestId : undefined; const message = error instanceof ApiError ? error.message : "This member could not be loaded."; return <EmptyState title="Member could not be loaded" detail={requestId ? `${message} Request ID: ${requestId}` : message} action={<Button onClick={retry}>Try again</Button>} />; }
function MemberDetailSkeleton() { return <div className="space-y-5"><Skeleton className="h-8 w-64" /><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-28" />)}</div></div>; }
