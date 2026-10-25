import { Rows3 } from "lucide-react";
import type { PropsWithChildren, ReactNode, TableHTMLAttributes } from "react";
import { cn } from "../lib";
import { Button } from "./Button";

export type PhoneColumnRole = "title" | "badge" | "meta" | "hidden";

export type ResponsiveColumn<Row> = {
  key: string;
  header: string;
  role: PhoneColumnRole;
  render: (row: Row) => ReactNode;
  numeric?: boolean;
};

export function ResponsiveTable<Row>({
  data,
  columns,
  getRowKey,
  onRowClick,
  dense = false,
}: {
  data: Row[];
  columns: ResponsiveColumn<Row>[];
  getRowKey: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  dense?: boolean;
}) {
  const visible = columns.filter((column) => column.role !== "hidden");
  const title = visible.find((column) => column.role === "title");
  const badge = visible.find((column) => column.role === "badge");
  const details = visible.filter((column) => column.role === "meta");
  const interactive = onRowClick
    ? "cursor-pointer hover:bg-[#F7F7F1] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
    : "";

  return (
    <>
      <div className="hidden overflow-hidden rounded-card border border-border md:block">
        <table
          className={cn(
            "w-full text-left text-sm tabular-nums",
            dense ? "[&_td]:py-2" : "[&_td]:py-3",
          )}
        >
          <thead className="border-b border-border bg-[#FAF9F6] text-[11px] font-medium uppercase tracking-[0.12em] text-muted">
            <tr>
              {visible.map((column) => (
                <th
                  key={column.key}
                  className={cn("px-3 py-3 text-left", column.numeric && "text-right")}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr
                key={getRowKey(row)}
                className={interactive}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={() => onRowClick?.(row)}
                onKeyDown={(event) => {
                  if (onRowClick && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    onRowClick(row);
                  }
                }}
              >
                {visible.map((column) => (
                  <td
                    key={column.key}
                    className={cn("border-b border-border px-3", column.numeric && "text-right")}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-3 md:hidden">
        {data.map((row) => (
          <article
            key={getRowKey(row)}
            className={cn(
              "rounded-card border border-border bg-surface p-4 shadow-card",
              interactive,
            )}
            tabIndex={onRowClick ? 0 : undefined}
            onClick={() => onRowClick?.(row)}
            onKeyDown={(event) => {
              if (onRowClick && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                onRowClick(row);
              }
            }}
          >
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0">{title?.render(row)}</div>
              <div className="shrink-0">{badge?.render(row)}</div>
            </div>
            <dl className="mt-4 grid gap-3">
              {details.map((column) => (
                <div key={column.key} className="flex min-w-0 items-baseline justify-between gap-4">
                  <dt className="shrink-0 text-[11px] uppercase tracking-[0.1em] text-muted">
                    {column.header}
                  </dt>
                  <dd
                    className={cn(
                      "min-w-0 text-right text-sm",
                      column.numeric && "whitespace-nowrap tabular-nums",
                    )}
                  >
                    {column.render(row)}
                  </dd>
                </div>
              ))}
            </dl>
          </article>
        ))}
      </div>
    </>
  );
}

export function Table({
  children,
  className,
  dense = false,
  ...props
}: PropsWithChildren<TableHTMLAttributes<HTMLTableElement> & { dense?: boolean }>) {
  return (
    <table
      className={cn("w-full text-left text-sm tabular-nums", dense && "[&_td]:py-2", className)}
      {...props}
    >
      {children}
    </table>
  );
}

export function TableHead({ children }: PropsWithChildren) {
  return (
    <thead className="border-b border-border bg-[#FAF9F6] text-[11px] font-medium uppercase tracking-[0.12em] text-muted [&_th]:px-3 [&_th]:py-3 [&_th]:text-left">
      {children}
    </thead>
  );
}

export function TableCell({ children, numeric = false }: PropsWithChildren<{ numeric?: boolean }>) {
  return (
    <td
      className={cn(
        "border-b border-border px-3 py-3",
        numeric && "whitespace-nowrap text-right tabular-nums",
      )}
    >
      {children}
    </td>
  );
}

export function TableDensityToggle({
  dense,
  onChange,
}: {
  dense: boolean;
  onChange: (dense: boolean) => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-pressed={dense}
      onClick={() => onChange(!dense)}
    >
      <Rows3 className="size-4" />
      {dense ? "Comfortable rows" : "Compact rows"}
    </Button>
  );
}
