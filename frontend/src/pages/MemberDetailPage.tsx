import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError, getMember, request } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { WhyPopover } from "../ui/Overlay";

type Session = { roles: string[] };

const balanceNames: Array<[string, string]> = [
  ["savings", "Savings"],
  ["shares", "Shares"],
  ["dues", "Dues paid"],
  ["locked", "Locked"],
  ["available", "Available"],
];

export default function MemberDetailPage() {
  const { memberId } = useParams();
  const detail = useQuery({
    queryKey: ["member", memberId],
    queryFn: () => getMember(memberId ?? ""),
    enabled: Boolean(memberId),
  });
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
  });

  if (detail.isPending) return <MemberDetailSkeleton />;
  if (detail.isError || !detail.data)
    return <MemberError error={detail.error} retry={() => void detail.refetch()} />;

  const member = detail.data;
  const canCreateBindingCode =
    session.data?.roles.some((role) => role === "admin" || role === "officer") ?? false;

  return (
    <>
      <header className="mb-6">
        <Link className="text-sm text-primary hover:underline" to="/members">
          Members
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="font-serif text-xl font-semibold tracking-tight">{member.full_name}</h1>
          <span className="text-sm text-muted">{member.member_no}</span>
          <Badge>{member.status}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted">
          Joined <DateTime value={member.joined_on} />
        </p>
      </header>

      <Tabs.Root defaultValue="overview">
        <Tabs.List
          className="mb-5 flex gap-1 overflow-x-auto border-b border-border"
          aria-label="Member record sections"
        >
          {[
            ["overview", "Overview"],
            ["loans", "Loans"],
            ["channels", "Channels"],
            ["ledger", "Ledger"],
            ["audit", "Audit"],
          ].map(([value, label]) => (
            <Tabs.Trigger
              key={value}
              value={value}
              className="border-b-2 border-transparent px-3 py-2 text-sm text-muted data-[state=active]:border-primary data-[state=active]:font-medium data-[state=active]:text-ink"
            >
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        <Tabs.Content value="overview">
          <Overview member={member} />
        </Tabs.Content>
        <Tabs.Content value="loans">
          <Loans />
        </Tabs.Content>
        <Tabs.Content value="channels">
          <Channels canCreateBindingCode={canCreateBindingCode} />
        </Tabs.Content>
        <Tabs.Content value="ledger">
          <ComingSoon
            title="Ledger coming soon"
            detail="Ledger entries for this member will appear here."
          />
        </Tabs.Content>
        <Tabs.Content value="audit">
          <ComingSoon
            title="Audit coming soon"
            detail="Audit evidence for this member will appear here."
          />
        </Tabs.Content>
      </Tabs.Root>
    </>
  );
}

function Overview({ member }: { member: Awaited<ReturnType<typeof getMember>> }) {
  const savings = member.balances.savings ?? 0;
  const locked = member.balances.locked ?? 0;
  const available = member.balances.available;

  return (
    <div className="space-y-5">
      <section className="grid items-start gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {balanceNames.map(([key, label]) => (
          <Card key={key} className="p-4">
            <p className="text-sm text-muted">{label}</p>
            <div className="mt-2 flex items-center justify-between gap-2">
              {member.balances[key] == null ? (
                <span>—</span>
              ) : (
                <Money kobo={member.balances[key]} className="text-lg font-semibold" />
              )}
              {key === "available" && (
                <WhyPopover
                  steps={[
                    `Savings: ₦${(savings / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                    `Locked: ₦${(locked / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                    `Available: ₦${((available ?? savings - locked) / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                  ]}
                />
              )}
            </div>
          </Card>
        ))}
      </section>

      <section className="grid items-start gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-serif text-lg font-semibold">Current salary</h2>
          <p className="mt-3 text-sm text-muted">
            Salary basis, amount and effective date are not available from this read-only endpoint.
          </p>
        </Card>
        <Card>
          <h2 className="font-serif text-lg font-semibold">Contact details</h2>
          <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
            <Item label="Phone" value={member.phone_e164 ?? "—"} />
            <Item label="Email" value={member.email ?? "—"} />
            <Item label="Staff no." value={member.staff_no ?? "—"} />
            <Item label="Employer" value={member.employer ?? "—"} />
          </dl>
        </Card>
      </section>

      <Card>
        <h2 className="font-serif text-lg font-semibold">Bank accounts</h2>
        {member.bank_accounts.length ? (
          <ul className="mt-4 space-y-3 text-sm">
            {member.bank_accounts.map((account) => (
              <li
                key={`${account.bank_code}-${account.last4}`}
                className="flex flex-wrap items-center gap-2"
              >
                <span>
                  {account.bank_code} · •••• {account.last4}
                </span>
                <Badge>{account.status}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-muted">No bank account on file.</p>
        )}
      </Card>
    </div>
  );
}

function Loans() {
  return (
    <EmptyState
      title="Loan records are unavailable"
      detail="This member endpoint does not yet provide applications or loans."
    />
  );
}

function Channels({ canCreateBindingCode }: { canCreateBindingCode: boolean }) {
  return (
    <Card>
      <h2 className="font-serif text-lg font-semibold">Channel bindings</h2>
      <p className="mt-2 text-sm text-muted">
        Binding status and dates are not available from the current API.
      </p>
      {canCreateBindingCode && (
        <Button className="mt-4" disabled title="Coming soon">
          Create binding code — Coming soon
        </Button>
      )}
    </Card>
  );
}

function ComingSoon({ title, detail }: { title: string; detail: string }) {
  return <EmptyState title={title} detail={detail} />;
}

function Item({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 font-medium">{value}</dd>
    </div>
  );
}

function MemberError({ error, retry }: { error: unknown; retry: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  const message = error instanceof ApiError ? error.message : "This member could not be loaded.";
  return (
    <EmptyState
      title="Member could not be loaded"
      detail={requestId ? `${message} Request ID: ${requestId}` : message}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function MemberDetailSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
    </div>
  );
}
