import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getMember, getMemberActivity, getMemberLoans, getMemberStatement, request } from "../api/client";
import { canSeeMemberFinancialActions } from "../memberFinancialPolicy.js";
import type { MemberActivityPage, MemberLoanPage, MemberStatementPage } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
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
      <div className="mb-5 sm:hidden"><label className="mb-1 block text-sm font-medium" htmlFor="member-section">Section</label><Select id="member-section" value={section} onChange={(event) => changeSection(event.target.value as Section)}>{sections.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></div>
      <Tabs.List className="mb-5 hidden flex-wrap gap-1 border-b border-border sm:flex" aria-label="Member record sections">{sections.map(([value, label]) => <Tabs.Trigger key={value} value={value} className="border-b-2 border-transparent px-3 py-2 text-sm text-muted data-[state=active]:border-primary data-[state=active]:font-medium data-[state=active]:text-ink">{label}</Tabs.Trigger>)}</Tabs.List>
      <Tabs.Content value="overview"><Overview member={member} /></Tabs.Content>
      <Tabs.Content value="statement"><Statement query={tabQuery.data as MemberStatementPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      <Tabs.Content value="loans"><Loans query={tabQuery.data as MemberLoanPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      <Tabs.Content value="activity"><Activity query={tabQuery.data as MemberActivityPage | undefined} isPending={tabQuery.isPending} isError={tabQuery.isError} page={page} setPage={setPage} /></Tabs.Content>
      <Tabs.Content value="actions"><Actions canSeeFinancialActions={canSeeMemberFinancialActions(session.data?.roles ?? [])} /></Tabs.Content>
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
function Statement({ query, isPending, isError, page, setPage }: TabProps<MemberStatementPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Statement unavailable" detail="The member statement could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Date</th><th className="p-3">Type</th><th className="p-3">Amount</th><th className="p-3">Running balance</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.id} className="border-b border-border"><td className="p-3"><DateTime value={item.date} /></td><td className="p-3 capitalize">{item.type}</td><td className="p-3"><Money kobo={item.amount_kobo} /></td><td className="p-3"><Money kobo={item.running_balance_kobo} /></td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No ledger entries.</p>}</div></Card></Paginated>; }
function Loans({ query, isPending, isError, page, setPage }: TabProps<MemberLoanPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Loans unavailable" detail="The member loans could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Application</th><th className="p-3">Status</th><th className="p-3">Principal</th><th className="p-3">Outstanding</th><th className="p-3">Next instalment</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.application_id} className="border-b border-border"><td className="p-3"><Link className="text-primary underline" to={`/loans/${item.application_id}`}>View loan</Link></td><td className="p-3"><Badge>{item.loan_status ?? item.application_status}</Badge></td><td className="p-3"><Money kobo={item.principal_kobo} /></td><td className="p-3">{item.outstanding_balance_kobo == null ? "—" : <Money kobo={item.outstanding_balance_kobo} />}</td><td className="p-3">{item.next_installment_at ? <><DateTime value={item.next_installment_at} /> · <Money kobo={item.next_installment_amount_kobo ?? 0} /></> : "—"}</td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No loans or applications.</p>}</div></Card></Paginated>; }
function Activity({ query, isPending, isError, page, setPage }: TabProps<MemberActivityPage>) { if (isPending) return <TabSkeleton />; if (isError) return <ComingSoon title="Activity unavailable" detail="Member activity could not be loaded." />; return <Paginated page={page} setPage={setPage} total={query?.total ?? 0} pageSize={query?.page_size ?? 25}><Card><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-border"><th className="p-3">Time</th><th className="p-3">Action</th><th className="p-3">Actor</th></tr></thead><tbody>{query?.items.map((item) => <tr key={item.event_id} className="border-b border-border"><td className="p-3"><DateTime value={item.timestamp} /></td><td className="p-3">{item.action}</td><td className="p-3">{item.actor_display_name}</td></tr>)}</tbody></table>{!query?.items.length && <p className="p-4 text-sm text-muted">No activity entries.</p>}</div></Card></Paginated>; }
function Paginated({ page, setPage, total, pageSize, children }: { page: number; setPage: (page: number) => void; total: number; pageSize: number; children: React.ReactNode }) { const totalPages = Math.max(1, Math.ceil(total / pageSize)); return <div className="space-y-3">{children}<div className="flex items-center justify-between text-sm text-muted"><span>{total} records</span><div className="flex gap-2"><Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><span className="px-2 py-2">Page {page} of {totalPages}</span><Button variant="secondary" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button></div></div></div>; }
function ComingSoon({ title, detail }: { title: string; detail: string }) { return <EmptyState title={title} detail={detail} />; }
function Actions({ canSeeFinancialActions }: { canSeeFinancialActions: boolean }) { void canSeeFinancialActions; return <ComingSoon title="Coming soon" detail="Member actions are not available yet." />; }
function Item({ label, value }: { label: string; value: React.ReactNode }) { return <div><dt className="text-xs uppercase tracking-wide text-muted">{label}</dt><dd className="mt-1 font-medium">{value}</dd></div>; }
function TabSkeleton() { return <div className="space-y-3"><Skeleton className="h-40 w-full" /><Skeleton className="h-8 w-40" /></div>; }
function MemberError({ error, retry }: { error: unknown; retry: () => void }) { const requestId = error instanceof ApiError ? error.requestId : undefined; const message = error instanceof ApiError ? error.message : "This member could not be loaded."; return <EmptyState title="Member could not be loaded" detail={requestId ? `${message} Request ID: ${requestId}` : message} action={<Button onClick={retry}>Try again</Button>} />; }
function MemberDetailSkeleton() { return <div className="space-y-5"><Skeleton className="h-8 w-64" /><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-28" />)}</div></div>; }
