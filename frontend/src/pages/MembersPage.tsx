import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, getMembers } from "../api/client";
import type { MemberListItem } from "../api/schema";
import { Avatar } from "../ui/Avatar";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { ResponsiveTable, TableDensityToggle, type ResponsiveColumn } from "../ui/Table";

const perPage = 15;

function useDebouncedValue(value: string, delay = 300): string {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);

  return debounced;
}

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  const message = error instanceof ApiError ? error.message : "Members could not be loaded.";

  return (
    <EmptyState
      title="Members could not be loaded"
      detail={requestId ? `${message} Request ID: ${requestId}` : message}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

export default function MembersPage({ fakeMembers }: { fakeMembers?: MemberListItem[] }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(0);
  const [dense, setDense] = useState(false);
  const debouncedSearch = useDebouncedValue(search);
  const members = useQuery({
    queryKey: ["members", fakeMembers ? "design" : "live", debouncedSearch],
    queryFn: () =>
      fakeMembers ? Promise.resolve(fakeMembers) : getMembers(debouncedSearch || undefined),
  });
  const statuses = useMemo(
    () =>
      [...new Set((members.data ?? []).map((member) => member.status))].filter(
        (status): status is string => typeof status === "string" && status.length > 0,
      ),
    [members.data],
  );
  const filtered = useMemo(() => {
    const term = debouncedSearch.trim().toLowerCase();
    return (members.data ?? []).filter((member) => {
      const searchable = [member.full_name, member.member_no, member.phone_e164, member.staff_no]
        .filter(Boolean)
        .join(" ");
      return (
        (status === "all" || member.status === status) &&
        (fakeMembers ? searchable.toLowerCase().includes(term) : true)
      );
    });
  }, [debouncedSearch, fakeMembers, members.data, status]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const pageMembers = filtered.slice(page * perPage, page * perPage + perPage);
  const visibleColumns = memberColumns.filter((column) => {
    if (column.key === "staffNo")
      return pageMembers.some((member) => member.staff_no !== undefined);
    if (column.key === "phone")
      return pageMembers.some((member) => member.phone_e164 !== undefined);
    if (column.key === "status") return pageMembers.some((member) => member.status !== undefined);
    return true;
  });

  useEffect(() => setPage(0), [debouncedSearch, status]);
  useEffect(() => setPage((current) => Math.min(current, totalPages - 1)), [totalPages]);

  return (
    <>
      <PageHeader title="Members" breadcrumb="Cooperative records">
        {fakeMembers && <Badge>Sample data</Badge>}
      </PageHeader>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          aria-label="Search members"
          placeholder="Search member name or number."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {statuses.length > 0 && (
          <Select
            aria-label="Filter by status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">All statuses</option>
            {statuses.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </Select>
        )}
        <div className="hidden md:block">
          <TableDensityToggle dense={dense} onChange={setDense} />
        </div>
      </div>

      {members.isPending && <MemberSkeleton dense={dense} />}
      {members.isError && <ErrorState error={members.error} retry={() => void members.refetch()} />}
      {members.data && filtered.length === 0 && (
        <EmptyState
          title="No members found"
          detail="Try a different member name, member number or status."
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setSearch("");
                setStatus("all");
              }}
            >
              Clear filters
            </Button>
          }
        />
      )}
      {pageMembers.length > 0 && (
        <>
          <ResponsiveTable
            data={pageMembers}
            columns={visibleColumns}
            getRowKey={(member) => member.id}
            dense={dense}
          />
          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span>{filtered.length} members</span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </Button>
              <span>
                Page {page + 1} of {totalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages - 1}
                onClick={() => setPage(page + 1)}
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

const memberColumns: ResponsiveColumn<MemberListItem>[] = [
  { key: "memberNo", header: "Member no.", role: "meta", render: (member) => member.member_no },
  {
    key: "name",
    header: "Name",
    role: "title",
    render: (member) => (
      <span className="flex min-w-0 items-center gap-3">
        <Avatar name={member.full_name} />
        <Link
          to={`/members/${member.id}`}
          className="block truncate font-medium after:absolute after:inset-0 focus-visible:rounded-control focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
        >
          {member.full_name}
        </Link>
      </span>
    ),
  },
  {
    key: "staffNo",
    header: "Staff no.",
    role: "hidden",
    render: (member) => member.staff_no ?? "—",
  },
  { key: "phone", header: "Phone", role: "meta", render: (member) => member.phone_e164 ?? "—" },
  {
    key: "status",
    header: "Status",
    role: "badge",
    render: (member) => <Badge>{member.status}</Badge>,
  },
  { key: "savings", header: "Savings", role: "hidden", numeric: true, render: () => "—" },
  { key: "available", header: "Available", role: "hidden", numeric: true, render: () => "—" },
  { key: "loan", header: "Active loan", role: "meta", render: () => <Badge>Not available</Badge> },
];

function MemberSkeleton({ dense }: { dense: boolean }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className={dense ? "h-16" : "h-20"} />
      ))}
    </div>
  );
}
