import type { PropsWithChildren, TableHTMLAttributes } from "react";
import { Rows3 } from "lucide-react";
import { Button } from "./Button";
import { cn } from "../lib";
export function Table({
  children,
  className,
  dense = false,
  ...props
}: PropsWithChildren<TableHTMLAttributes<HTMLTableElement> & { dense?: boolean }>) {
  return (
    <div className="overflow-x-auto rounded-card border border-border">
      <table
        className={cn(
          "w-full text-left text-sm tabular-nums",
          dense ? "[&_td]:h-9 [&_th]:h-9" : "[&_td]:h-11 [&_th]:h-11",
          className,
        )}
        {...props}
      >
        {children}
      </table>
    </div>
  );
}
export function TableHead({ children }: PropsWithChildren) {
  return (
    <thead className="sticky top-0 z-10 border-b border-border bg-bg text-xs font-medium text-muted [&_th]:px-3 [&_th]:text-left">
      {children}
    </thead>
  );
}
export function TableCell({ children, numeric = false }: PropsWithChildren<{ numeric?: boolean }>) {
  return (
    <td className={cn("border-b border-border px-3", numeric && "text-right tabular-nums")}>
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
