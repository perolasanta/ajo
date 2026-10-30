import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import type { MemberBalances } from "../api/schema";
import { ApiError, getMember, request } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { WhyPopover } from "../ui/Overlay";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";

type Session = { roles: string[] };

const balanceNames: Array<[keyof MemberBalances, string]> = [
  ["savings_kobo", "Savings"],
  ["shares_kobo", "Shares"],
  ["dues_paid_kobo", "Dues paid"],
  ["locked_kobo", "Locked"],
  ["available_kobo", "Available"],
];

const sections = [
  ["overview", "Overview"],
  ["loans", "Loans"],
  ["channels", "Channels"],
  ["ledger", "Ledger"],
  ["audit", "Audit"],
] as const;

export default function MemberDetailPage() {
  const { memberId } = useParams();
  const [section, setSection] = useState<(typeof sections)[number][0]>("overview");
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
      <PageHeader title={member.full_name} breadcrumb={`Members / ${member.full_name}`}>
        <div className="flex flex-wrap items-center gap-2">
          {member.status && <Badge>{member.status}</Badge>}
          <Button variant="secondary" disabled title="Member actions are not available yet.">
            Actions coming soon
          </Button>
        </div>
      </PageHeader>
      <p className="-mt-4 mb-6 text-sm text-muted">
        {member.member_no}
        {member.joined_on && (
          <>
            {" "}
            · Joined <DateTime value={member.joined_on} />
          </>
        )}
      </p>

      <Tabs.Root value={section} onValueChange={(value) => setSection(value as typeof section)}>
        <div className="mb-5 sm:hidden">
          <label className="mb-1 block text-sm font-medium" htmlFor="member-section">
            Section
          </label>
          <Select
            id="member-section"
            value={section}
            onChange={(event) => setSection(event.target.value as typeof section)}
          >
            {sections.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </div>
        <Tabs.List
          className="mb-5 hidden flex-wrap gap-1 border-b border-border sm:flex"
          aria-label="Member record sections"
        >
          {sections.map(([value, label]) => (
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
            detail="Ledger entries will appear here once the ledger screen is added."
          />
        </Tabs.Content>
        <Tabs.Content value="audit">
          <ComingSoon
            title="Audit coming soon"
            detail="Audit entries will appear here once the audit screen is added."
          />
        </Tabs.Content>
      </Tabs.Root>
    </>
  );
}

function Overview({ member }: { member: Awaited<ReturnType<typeof getMember>> }) {
  const balances = member.balances;
  const bankAccounts = member.bank_accounts;
  const hasContactDetails =
    member.phone_e164 !== undefined ||
    member.email !== undefined ||
    member.staff_no !== undefined ||
    member.employer !== undefined;

  return (
    <div className="space-y-5">
      {balances && (
        <section className="grid items-start gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {balanceNames.map(([key, label]) => (
            <Card key={key} className="p-4">
              <p className="text-sm text-muted">{label}</p>
              <div className="mt-2 flex items-center justify-between gap-2">
                {balances[key] == null ? (
                  <span>—</span>
                ) : (
                  <Money kobo={balances[key]} className="text-lg font-semibold" />
                )}
                {key === "available_kobo" && (
                  <WhyPopover
                    steps={[
                      `Savings: ₦${(balances.savings_kobo / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                      `Locked: ₦${(balances.locked_kobo / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                      `Available: ₦${(balances.available_kobo / 100).toLocaleString("en-NG", { minimumFractionDigits: 2 })}`,
                    ]}
                  />
                )}
              </div>
            </Card>
          ))}
        </section>
      )}

      <section className="grid items-start gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-serif text-lg font-semibold">Current salary</h2>
          <p className="mt-3 text-sm text-muted">
            Salary basis, amount and effective date are not available from this read-only endpoint.
          </p>
        </Card>
        {hasContactDetails && (
          <Card>
            <h2 className="font-serif text-lg font-semibold">Contact details</h2>
            <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
              <Item label="Phone" value={member.phone_e164 ?? "—"} />
              <Item label="Email" value={member.email ?? "—"} />
              <Item label="Staff no." value={member.staff_no ?? "—"} />
              <Item label="Employer" value={member.employer ?? "—"} />
            </dl>
          </Card>
        )}
      </section>

      {bankAccounts !== undefined && (
        <Card>
          <h2 className="font-serif text-lg font-semibold">Bank accounts</h2>
          {bankAccounts.length ? (
            <ul className="mt-4 space-y-3 text-sm">
              {bankAccounts.map((account) => (
                <li
                  key={`${account.bank_code}-${account.last4}`}
                  className="grid gap-1 sm:grid-cols-2"
                >
                  <div>
                    <p className="font-medium">Bank: {account.bank_code}</p>
                    <p className="text-muted">•••• {account.last4}</p>
                    {account.account_name && <p className="text-muted">{account.account_name}</p>}
                  </div>
                  <p className="sm:text-right">
                    {account.verified_at ? (
                      <>
                        Verified <DateTime value={account.verified_at} />
                      </>
                    ) : account.status ? (
                      account.status === "verified" ? (
                        "Verified"
                      ) : (
                        "Pending verification"
                      )
                    ) : null}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted">No bank account on file.</p>
          )}
        </Card>
      )}
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
