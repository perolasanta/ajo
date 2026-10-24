import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, getMembers } from "../api/client";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { Table, TableCell, TableDensityToggle, TableHead } from "../ui/Table";

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

export default function MembersPage() {
  const navigate = useNavigate();
  const members = useQuery({ queryKey: ["members"], queryFn: getMembers });
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(0);
  const [dense, setDense] = useState(false);
  const debouncedSearch = useDebouncedValue(search);
  const statuses = useMemo(
    () => [...new Set((members.data ?? []).map((member) => member.status))],
    [members.data],
  );
  const filtered = useMemo(() => {
    const term = debouncedSearch.trim().toLowerCase();
    return (members.data ?? []).filter((member) => {
      const searchable = [member.full_name, member.member_no, member.phone_e164]
        .filter(Boolean)
        .join(" ");
      return (
        (status === "all" || member.status === status) && searchable.toLowerCase().includes(term)
      );
    });
  }, [debouncedSearch, members.data, status]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const pageMembers = filtered.slice(page * perPage, page * perPage + perPage);

  useEffect(() => setPage(0), [debouncedSearch, status]);
  useEffect(() => setPage((current) => Math.min(current, totalPages - 1)), [totalPages]);

  return (
    <>
      <PageHeader title="Members" breadcrumb="Cooperative records" />
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          aria-label="Search members"
          placeholder="Search name, staff no. or phone"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
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
        <TableDensityToggle dense={dense} onChange={setDense} />
      </div>

      {members.isPending && <MemberSkeleton dense={dense} />}
      {members.isError && <ErrorState error={members.error} retry={() => void members.refetch()} />}
      {members.data && filtered.length === 0 && (
        <EmptyState
          title="No members found"
          detail="Try a different name, staff number, phone number or status."
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
          <Table dense={dense}>
            <TableHead>
              <tr>
                <th>Member no.</th>
                <th>Name</th>
                <th>Staff no.</th>
                <th>Phone</th>
                <th>Status</th>
                <th className="text-right">Savings</th>
                <th className="text-right">Available</th>
                <th>Active loan</th>
              </tr>
            </TableHead>
            <tbody>
              {pageMembers.map((member) => (
                <tr
                  key={member.id}
                  className="cursor-pointer hover:bg-bg focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                  tabIndex={0}
                  onClick={() => navigate(`/members/${member.id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      navigate(`/members/${member.id}`);
                    }
                  }}
                >
                  <TableCell>{member.member_no}</TableCell>
                  <TableCell>
                    <span className="font-medium">{member.full_name}</span>
                  </TableCell>
                  <TableCell>—</TableCell>
                  <TableCell>{member.phone_e164 ?? "—"}</TableCell>
                  <TableCell>
                    <Badge>{member.status}</Badge>
                  </TableCell>
                  <TableCell numeric>—</TableCell>
                  <TableCell numeric>—</TableCell>
                  <TableCell>
                    <Badge>Not available</Badge>
                  </TableCell>
                </tr>
              ))}
            </tbody>
          </Table>
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

function MemberSkeleton({ dense }: { dense: boolean }) {
  return (
    <Table dense={dense}>
      <TableHead>
        <tr>
          <th>Member no.</th>
          <th>Name</th>
          <th>Staff no.</th>
          <th>Phone</th>
          <th>Status</th>
          <th className="text-right">Savings</th>
          <th className="text-right">Available</th>
          <th>Active loan</th>
        </tr>
      </TableHead>
      <tbody>
        {Array.from({ length: 6 }, (_, index) => (
          <tr key={index}>
            <TableCell>
              <Skeleton className="h-4 w-20" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-32" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-16" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-24" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-20" />
            </TableCell>
            <TableCell numeric>
              <Skeleton className="ml-auto h-4 w-20" />
            </TableCell>
            <TableCell numeric>
              <Skeleton className="ml-auto h-4 w-20" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-20" />
            </TableCell>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
