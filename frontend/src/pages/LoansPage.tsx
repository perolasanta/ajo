import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  getLoanApplications,
  getLoanProducts,
  getMember,
  getMembers,
  submitLoanApplication,
} from "../api/client";
import type { MemberDetail } from "../api/schema";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { DateTime, Money } from "../ui/Format";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { PageHeader } from "../ui/PageHeader";
import { Select } from "../ui/Select";
import { ResponsiveTable, type ResponsiveColumn } from "../ui/Table";
import { canSubmitLoans } from "../loanPolicy";

const pageSize = 10;

type Session = { roles: string[] };

function useDebouncedValue(value: string, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);
  return debounced;
}

function ErrorState({
  error,
  retry,
  title = "Loans could not be loaded",
}: {
  error: unknown;
  retry: () => void;
  title?: string;
}) {
  const message = error instanceof ApiError ? error.message : "Please try again.";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <EmptyState
      title={title}
      detail={`${message}${requestId ? ` Request ID: ${requestId}` : ""}`}
      action={<Button onClick={retry}>Try again</Button>}
    />
  );
}

export default function LoansPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search);
  const session = useQuery({ queryKey: ["session"], queryFn: () => requestSession() });
  const loans = useQuery({
    queryKey: ["loan-applications", debouncedSearch, status, page],
    queryFn: () =>
      getLoanApplications({ search: debouncedSearch || undefined, status, page, pageSize }),
  });
  const statuses = useMemo(
    () => [...new Set((loans.data?.items ?? []).map((item) => item.status))],
    [loans.data],
  );
  const canSubmit = canSubmitLoans(session.data?.roles ?? []);
  const totalPages = Math.max(1, Math.ceil((loans.data?.total ?? 0) / pageSize));
  useEffect(() => setPage(1), [debouncedSearch, status]);

  return (
    <>
      <PageHeader title="Loans" breadcrumb="Cooperative records">
        {canSubmit && (
          <Link
            className="inline-flex h-10 items-center rounded-control bg-primary px-3.5 text-sm font-semibold text-white"
            to="/loans/new"
          >
            New application
          </Link>
        )}
      </PageHeader>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          aria-label="Search loans"
          placeholder="Search member name or number."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          aria-label="Filter loans by status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">All statuses</option>
          {statuses.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </Select>
      </div>
      {loans.isPending && <LoansSkeleton />}
      {loans.isError && <ErrorState error={loans.error} retry={() => void loans.refetch()} />}
      {loans.data && loans.data.items.length === 0 && (
        <EmptyState
          title="No loan applications found"
          detail="Try another search or status filter."
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
      {loans.data && loans.data.items.length > 0 && (
        <>
          <ResponsiveTable
            data={loans.data.items}
            columns={loanColumns}
            getRowKey={(loan) => loan.id}
          />
          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span>{loans.data.total} applications</span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous
              </Button>
              <span>
                Page {page} of {totalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((value) => value + 1)}
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

const loanColumns: ResponsiveColumn<
  Awaited<ReturnType<typeof getLoanApplications>>["items"][number]
>[] = [
  {
    key: "member",
    header: "Member",
    role: "title",
    render: (loan) => (
      <span className="block min-w-0">
        <Link
          to={`/loans/${loan.id}`}
          className="block truncate font-medium after:absolute after:inset-0"
        >
          {loan.full_name}
        </Link>
        <span className="block text-xs text-muted">{loan.member_no}</span>
      </span>
    ),
  },
  { key: "product", header: "Product", role: "meta", render: (loan) => loan.product_name },
  {
    key: "amount",
    header: "Amount",
    role: "meta",
    numeric: true,
    render: (loan) => <Money kobo={loan.principal_kobo} />,
  },
  { key: "tenor", header: "Tenor", role: "meta", render: (loan) => `${loan.tenor_months} months` },
  {
    key: "status",
    header: "Status",
    role: "badge",
    render: (loan) => <Badge>{loan.status}</Badge>,
  },
  {
    key: "date",
    header: "Submitted",
    role: "meta",
    render: (loan) => <DateTime value={loan.submitted_at} />,
  },
];

function LoansSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }, (_, index) => (
        <Skeleton key={index} className="h-20" />
      ))}
    </div>
  );
}

async function requestSession(): Promise<Session> {
  const response = await fetch("/api/v1/auth/me", { credentials: "include" });
  if (!response.ok) throw new Error("Session unavailable");
  return response.json() as Promise<Session>;
}

export function NewLoanApplicationPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [memberId, setMemberId] = useState("");
  const [productId, setProductId] = useState("");
  const [amount, setAmount] = useState("");
  const [tenor, setTenor] = useState("");
  const [accountId, setAccountId] = useState("");
  const [apiError, setApiError] = useState<string | null>(null);
  const members = useQuery({ queryKey: ["members", "loan-picker"], queryFn: () => getMembers() });
  const products = useQuery({ queryKey: ["loan-products"], queryFn: getLoanProducts });
  const member = useQuery({
    queryKey: ["loan-member", memberId],
    queryFn: () => getMember(memberId),
    enabled: Boolean(memberId),
  });
  const mutation = useMutation({
    mutationFn: submitLoanApplication,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["loan-applications"] });
      navigate(`/loans/${result.application_id}`);
    },
    onError: (error) =>
      setApiError(
        error instanceof ApiError ? error.message : "The application could not be submitted.",
      ),
  });
  const selectedProduct = products.data?.find((product) => product.id === productId);
  const memberData: MemberDetail | undefined = member.data;
  const formError = !memberId
    ? "Choose a member."
    : !productId
      ? "Choose a loan product."
      : !amount || Number(amount) <= 0
        ? "Enter an amount greater than zero."
        : !tenor || Number(tenor) <= 0
          ? "Enter a tenor greater than zero."
          : undefined;
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setApiError(null);
    if (formError) return;
    mutation.mutate({
      member_id: memberId,
      product_id: productId,
      principal_kobo: Math.round(Number(amount) * 100),
      tenor_months: Number(tenor),
      destination_bank_account_id: accountId || undefined,
    });
  };

  return (
    <>
      <PageHeader title="New loan application" breadcrumb="Loans / New application" />
      <Card className="max-w-2xl">
        <form className="space-y-5" onSubmit={submit}>
          {apiError && (
            <p
              className="rounded-control border border-danger/30 bg-danger/5 p-3 text-sm text-danger"
              role="alert"
            >
              {apiError}
            </p>
          )}
          <Field
            label="Member"
            error={members.isError ? "Members could not be loaded." : undefined}
          >
            <Select
              value={memberId}
              onChange={(event) => {
                setMemberId(event.target.value);
                setAccountId("");
              }}
              disabled={members.isPending}
            >
              <option value="">Choose a member</option>
              {(members.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.full_name} · {item.member_no}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Loan product"
            error={products.isError ? "Loan products could not be loaded." : undefined}
          >
            <Select
              value={productId}
              onChange={(event) => setProductId(event.target.value)}
              disabled={products.isPending}
            >
              <option value="">Choose a product</option>
              {(products.data ?? []).map((product) => (
                <option key={product.id} value={product.id}>
                  {product.name} · {product.min_tenor_months}–{product.max_tenor_months} months
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field
              label="Amount (₦)"
              helper={
                selectedProduct
                  ? `Range: ₦${(selectedProduct.min_amount_kobo / 100).toLocaleString()}–₦${(selectedProduct.max_amount_kobo / 100).toLocaleString()}`
                  : undefined
              }
            >
              <Input
                inputMode="decimal"
                type="number"
                min="0"
                step="0.01"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            </Field>
            <Field
              label="Tenor (months)"
              helper={
                selectedProduct
                  ? `Range: ${selectedProduct.min_tenor_months}–${selectedProduct.max_tenor_months} months`
                  : undefined
              }
            >
              <Input
                inputMode="numeric"
                type="number"
                min="1"
                step="1"
                value={tenor}
                onChange={(event) => setTenor(event.target.value)}
              />
            </Field>
          </div>
          <Field
            label="Destination bank account"
            helper="Only the bank and last four digits are shown."
          >
            <Select
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
              disabled={!memberId || member.isPending}
            >
              <option value="">No destination account</option>
              {(memberData?.bank_accounts ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.bank_code} · •••• {account.last4}
                </option>
              ))}
            </Select>
          </Field>
          {formError && (
            <p className="text-sm text-danger" role="alert">
              {formError}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={mutation.isPending} disabled={Boolean(formError)}>
              {mutation.isPending ? "Submitting…" : "Submit application"}
            </Button>
            <Button type="button" variant="secondary" onClick={() => navigate("/loans")}>
              Cancel
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
