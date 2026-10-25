import type { HTMLAttributes, PropsWithChildren } from "react";
import { cn } from "../lib";
export function Card({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLElement>>) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-card border border-border bg-surface p-5 shadow-card",
        className,
      )}
      {...props}
    >
      {children}
    </section>
  );
}
