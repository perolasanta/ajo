import type { InputHTMLAttributes } from "react";
import { cn } from "../lib";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "block min-h-10 w-full rounded-card border border-border bg-surface px-3 text-sm placeholder:text-muted/70 disabled:cursor-not-allowed disabled:bg-bg aria-[invalid=true]:border-danger aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger/30",
        className,
      )}
      {...props}
    />
  );
}
