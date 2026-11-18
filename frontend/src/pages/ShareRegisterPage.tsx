import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getShareRegister } from "../api/client";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, Skeleton } from "../ui/Feedback";
import { Money } from "../ui/Format";
import { PageHeader } from "../ui/PageHeader";

const PAGE_SIZE = 25;
const numberFormat = new Intl.NumberFormat("en-NG");

export default function ShareRegisterPage() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => setPage(1), [debouncedSearch]);

  const register = useQuery({
    queryKey: ["share-register", debouncedSearch, page],
    queryFn: () =>
      getShareRegister({
        search: debouncedSearch || undefined,
        page,
        pageSize: PAGE_SIZE,
      }),
  });
  const totalPages = Math.max(1, Math.ceil((register.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-5">
      <PageHeader title="Share register" breadcrumb="Members / Share register" />
      {register.data && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Card>
            <p className="text-sm text-muted">Total shares</p>
            <p className="mt-2 text-2xl font-semibold">
              {numberFormat.format(register.data.total_shares)}
            </p>
          </Card>
          <Card>
            <p className="text-sm text-muted">Members holding shares</p>
            <p className="mt-2 text-2xl font-semibold">
              {numberFormat.format(register.data.members_holding_shares)}
            </p>
          </Card>
          <Card>
            <p className="text-sm text-muted">Total value at current price</p>
            <p className="mt-2 text-2xl font-semibold">
              <Money kobo={register.data.total_value_kobo} />
            </p>
          </Card>
          <Card>
            <p className="text-sm text-muted">Current price per share</p>
            <p className="mt-2 text-2xl font-semibold">
              <Money kobo={register.data.current_unit_price_naira * 100} />
            </p>
          </Card>
        </div>
      )}

      <label className="block max-w-xl text-sm font-medium">
        Search member name
        <input
          className="mt-1 w-full rounded-control border border-border bg-white p-2"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Enter a member name"
        />
      </label>

      {register.isPending && <Skeleton className="h-48 w-full" />}
      {register.isError && (
        <EmptyState
          title="Share register could not be loaded"
          detail={register.error instanceof Error ? register.error.message : "Please try again."}
        />
      )}
      {register.data && register.data.items.length === 0 && (
        <EmptyState
          title={debouncedSearch ? "No matching members" : "No members holding shares"}
          detail={
            debouncedSearch ? "Try another member name." : "There are no share holdings to show."
          }
        />
      )}
      {register.data && register.data.items.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="p-3">Member</th>
                  <th className="p-3">Shares held</th>
                  <th className="p-3">Value at current price</th>
                  <th className="p-3">Last purchase</th>
                </tr>
              </thead>
              <tbody>
                {register.data.items.map((item) => (
                  <tr key={item.member_id} className="border-b border-border">
                    <td className="p-3">{item.member_name}</td>
                    <td className="p-3">{numberFormat.format(item.shares_held)}</td>
                    <td className="p-3">
                      <Money kobo={item.value_kobo} />
                    </td>
                    <td className="p-3">{item.last_purchase_date ?? "No purchases"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-border p-3">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((value) => value - 1)}
            >
              Previous
            </Button>
            <p className="text-sm text-muted">
              Page {page} of {totalPages} · {numberFormat.format(register.data.total)} members
            </p>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              Next
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
