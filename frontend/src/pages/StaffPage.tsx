import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate } from "react-router-dom";
import {
  ApiError,
  createRoleChangeProposal,
  decideRoleChangeProposal,
  getRoleChangeProposals,
  getStaff,
  request,
} from "../api/client";
import type { RoleChangeProposal, StaffRole } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";

const pageSize = 25;
const proposalStatuses = [
  "pending",
  "approved",
  "rejected",
  "cancelled",
  "expired",
  "all",
] as const;
const roles: StaffRole[] = ["officer", "committee", "treasurer", "admin", "auditor"];
type ProposalStatus = (typeof proposalStatuses)[number];
type Session = { user_id: string; roles: string[] };

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function PageError({ title, error, retry }: { title: string; error: unknown; retry: () => void }) {
  return (
    <EmptyState
      title={title}
      detail={errorMessage(error, "Please try again.")}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

export default function StaffPage() {
  const queryClient = useQueryClient();
  const [staffPage, setStaffPage] = useState(1);
  const [proposalPage, setProposalPage] = useState(1);
  const [status, setStatus] = useState<ProposalStatus>("pending");
  const [targetUserId, setTargetUserId] = useState("");
  const [role, setRole] = useState<StaffRole>("officer");
  const [action, setAction] = useState<"add" | "remove">("add");
  const [reason, setReason] = useState("");
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => request<Session>("/auth/me"),
    retry: false,
  });
  const isAdmin = session.data?.roles.includes("admin") ?? false;
  const staff = useQuery({
    queryKey: ["staff", staffPage],
    queryFn: () => getStaff({ page: staffPage, pageSize }),
    enabled: isAdmin,
    retry: false,
  });
  const proposals = useQuery({
    queryKey: ["role-change-proposals", status, proposalPage],
    queryFn: () => getRoleChangeProposals({ status, page: proposalPage, pageSize }),
    enabled: isAdmin,
    retry: false,
  });
  const createProposal = useMutation({
    mutationFn: () =>
      createRoleChangeProposal({
        target_user_id: targetUserId,
        role,
        action,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["role-change-proposals"] });
    },
  });
  const proposalAction = useMutation({
    mutationFn: ({
      proposalId,
      decision,
    }: {
      proposalId: string;
      decision: "approve" | "reject" | "cancel";
    }) => decideRoleChangeProposal(proposalId, decision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["role-change-proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["staff"] });
      void queryClient.invalidateQueries({ queryKey: ["session"] });
    },
  });

  if (session.isPending) return <Skeleton className="h-60" />;
  if (session.isError || !session.data) return <Navigate to="/login" replace />;
  if (!isAdmin) return <Navigate to="/dashboard" replace />;

  const staffTotalPages = Math.max(1, Math.ceil((staff.data?.total ?? 0) / pageSize));
  const proposalTotalPages = Math.max(1, Math.ceil((proposals.data?.total ?? 0) / pageSize));

  return (
    <>
      <PageHeader title="Staff management" breadcrumb="Staff and role-change proposals" />
      <section className="mb-8 rounded-card border border-border bg-surface p-4 sm:p-5">
        <h2 className="font-serif text-xl font-semibold">Propose a role change</h2>
        <p className="mt-1 text-sm text-muted">
          Each proposal changes one role. A different active admin must approve it.
        </p>
        <form
          className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            createProposal.mutate();
          }}
        >
          <label className="text-sm font-medium">
            Staff member
            <Select
              className="mt-1 min-h-11 w-full"
              required
              value={targetUserId}
              onChange={(event) => setTargetUserId(event.target.value)}
              disabled={staff.isPending || staff.isError || !staff.data?.items.length}
            >
              <option value="" disabled>
                Select a staff member
              </option>
              {staff.data?.items.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.email} ({person.roles.join(", ") || "no role"})
                </option>
              ))}
            </Select>
          </label>
          <label className="text-sm font-medium">
            Role
            <Select
              className="mt-1 min-h-11 w-full"
              value={role}
              onChange={(event) => setRole(event.target.value as StaffRole)}
            >
              {roles.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-sm font-medium">
            Action
            <Select
              className="mt-1 min-h-11 w-full"
              value={action}
              onChange={(event) => setAction(event.target.value as "add" | "remove")}
            >
              <option value="add">Add role</option>
              <option value="remove">Remove role</option>
            </Select>
          </label>
          <label className="text-sm font-medium sm:col-span-2 xl:col-span-1">
            Reason
            <textarea
              className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 py-2 text-sm"
              required
              minLength={1}
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {staff.isError && (
            <p className="text-sm text-danger sm:col-span-2 xl:col-span-4" role="alert">
              {errorMessage(staff.error, "Staff could not be loaded.")}
            </p>
          )}
          {createProposal.isError && (
            <p className="text-sm text-danger sm:col-span-2 xl:col-span-4" role="alert">
              {errorMessage(
                createProposal.error,
                "The role-change proposal could not be submitted.",
              )}
            </p>
          )}
          {createProposal.isSuccess && (
            <p className="text-sm text-success sm:col-span-2 xl:col-span-4" role="status">
              Proposal submitted. It remains pending until a different admin reviews it.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2 xl:col-span-4">
            <Button
              type="submit"
              loading={createProposal.isPending}
              disabled={!targetUserId || staff.isPending || staff.isError}
            >
              Submit proposal
            </Button>
            <span className="text-xs text-muted">
              If only one active admin exists, the server will reject the proposal.
            </span>
          </div>
        </form>
      </section>

      <section className="mb-8">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-serif text-xl font-semibold">Staff</h2>
            <p className="mt-1 text-sm text-muted">
              Directory access is restricted to cooperative admins.
            </p>
          </div>
          {staff.data && staff.data.total > 0 && (
            <div className="flex items-center gap-2 text-sm">
              <Button
                variant="secondary"
                size="sm"
                disabled={staffPage <= 1}
                onClick={() => setStaffPage((value) => value - 1)}
              >
                Previous
              </Button>
              <span>
                Page {staffPage} of {staffTotalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={staffPage >= staffTotalPages}
                onClick={() => setStaffPage((value) => value + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </div>
        {staff.isPending && <Skeleton className="h-40" />}
        {staff.isError && (
          <PageError
            title="Staff could not be loaded"
            error={staff.error}
            retry={() => void staff.refetch()}
          />
        )}
        {staff.data && staff.data.items.length === 0 && (
          <EmptyState
            title="No staff records"
            detail="Staff accounts will appear here when provisioned."
          />
        )}
        {staff.data && staff.data.items.length > 0 && (
          <div className="overflow-hidden rounded-card border border-border bg-surface">
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[620px] text-left text-sm">
                <thead className="border-b border-border bg-[#FAF9F6] text-[11px] uppercase tracking-[0.12em] text-muted">
                  <tr>
                    <th className="px-4 py-3">Staff member</th>
                    <th className="px-4 py-3">Roles</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {staff.data.items.map((person) => (
                    <tr key={person.user_id} className="border-b border-border last:border-0">
                      <td className="break-all px-4 py-3">{person.email}</td>
                      <td className="px-4 py-3">{person.roles.join(", ") || "No role"}</td>
                      <td className="px-4 py-3">
                        <Badge>{person.status}</Badge>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {formatDate(person.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="space-y-3 p-3 md:hidden">
              {staff.data.items.map((person) => (
                <article key={person.user_id} className="rounded-card border border-border p-4">
                  <p className="break-all text-sm font-medium">{person.email}</p>
                  <p className="mt-2 text-sm text-muted">{person.roles.join(", ") || "No role"}</p>
                  <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted">
                    <Badge>{person.status}</Badge>
                    <span>{formatDate(person.created_at)}</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-serif text-xl font-semibold">Role-change proposals</h2>
            <p className="mt-1 text-sm text-muted">
              Actions are revalidated by the server against current roles and proposal state.
            </p>
          </div>
          <label className="text-sm font-medium">
            Status
            <Select
              className="mt-1 min-h-11 min-w-40"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as ProposalStatus);
                setProposalPage(1);
              }}
            >
              {proposalStatuses.map((value) => (
                <option key={value} value={value}>
                  {value === "all" ? "All statuses" : value}
                </option>
              ))}
            </Select>
          </label>
        </div>
        {proposalAction.isError && (
          <p
            className="mb-3 rounded-control border border-danger/30 p-3 text-sm text-danger"
            role="alert"
          >
            {errorMessage(
              proposalAction.error,
              "The requested action could not be completed. Refresh and review the current proposal state.",
            )}
          </p>
        )}
        {proposals.isPending && <Skeleton className="h-40" />}
        {proposals.isError && (
          <PageError
            title="Proposals could not be loaded"
            error={proposals.error}
            retry={() => void proposals.refetch()}
          />
        )}
        {proposals.data && proposals.data.items.length === 0 && (
          <EmptyState
            title={status === "pending" ? "No pending proposals" : "No proposals found"}
            detail="Proposals matching this status will appear here."
          />
        )}
        {proposals.data && proposals.data.items.length > 0 && (
          <>
            <div className="space-y-3">
              {proposals.data.items.map((proposal) => (
                <ProposalCard
                  key={proposal.proposal_id}
                  proposal={proposal}
                  currentUserId={session.data.user_id}
                  actionPending={proposalAction.isPending}
                  onAction={(decision) =>
                    proposalAction.mutate({ proposalId: proposal.proposal_id, decision })
                  }
                />
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
              <span>{proposals.data.total} proposals</span>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={proposalPage <= 1}
                  onClick={() => setProposalPage((value) => value - 1)}
                >
                  Previous
                </Button>
                <span>
                  Page {proposalPage} of {proposalTotalPages}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={proposalPage >= proposalTotalPages}
                  onClick={() => setProposalPage((value) => value + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}

function ProposalCard({
  proposal,
  currentUserId,
  actionPending,
  onAction,
}: {
  proposal: RoleChangeProposal;
  currentUserId: string;
  actionPending: boolean;
  onAction: (decision: "approve" | "reject" | "cancel") => void;
}) {
  const pending = proposal.status === "pending";
  const isProposer = proposal.proposer_user_id === currentUserId;
  const isTarget = proposal.target_user_id === currentUserId;
  const canApprove = pending && !isProposer && !(proposal.action === "add" && isTarget);
  return (
    <article className="rounded-card border border-border bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-words font-medium">
            {proposal.action === "add" ? "Add" : "Remove"} {proposal.role} ·{" "}
            {proposal.target_email ?? proposal.target_user_id}
          </h3>
          <p className="mt-1 break-all text-xs text-muted">
            Proposed by {proposal.proposer_email ?? proposal.proposer_user_id}
          </p>
        </div>
        <Badge status={proposal.status}>{proposal.status}</Badge>
      </div>
      <p className="mt-3 whitespace-pre-wrap break-words text-sm">{proposal.reason}</p>
      <dl className="mt-4 grid gap-3 text-xs text-muted sm:grid-cols-3">
        <div>
          <dt>Submitted</dt>
          <dd className="mt-1">{formatDate(proposal.created_at)}</dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd className="mt-1">{formatDate(proposal.expires_at)}</dd>
        </div>
        <div>
          <dt>Decision</dt>
          <dd className="mt-1">
            {proposal.decided_at ? formatDate(proposal.decided_at) : "Awaiting review"}
          </dd>
        </div>
      </dl>
      {pending && (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-3">
          <Button
            size="sm"
            disabled={!canApprove || actionPending}
            onClick={() => onAction("approve")}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={actionPending}
            onClick={() => onAction("reject")}
          >
            Reject
          </Button>
          {isProposer && (
            <Button
              size="sm"
              variant="secondary"
              disabled={actionPending}
              onClick={() => onAction("cancel")}
            >
              Cancel proposal
            </Button>
          )}
          {!canApprove && (
            <span className="self-center text-xs text-muted">
              {isProposer
                ? "The proposer cannot approve."
                : "The target cannot approve an added role."}
            </span>
          )}
        </div>
      )}
    </article>
  );
}
