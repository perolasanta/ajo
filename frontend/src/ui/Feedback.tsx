import { CheckCircle2, Inbox, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { cn } from "../lib";
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-label="Loading"
      className={cn("animate-pulse rounded-control bg-border", className)}
    />
  );
}
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-card border border-dashed border-border p-8 text-center">
      <Inbox className="mx-auto size-6 text-muted" aria-hidden="true" />
      <h2 className="mt-3 font-serif text-lg font-semibold">{title}</h2>
      <p className="mx-auto mt-1 max-w-sm text-sm text-muted">{detail}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
export function Toast({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (!onDismiss || paused) return;
    const timeout = window.setTimeout(onDismiss, 5_000);
    return () => window.clearTimeout(timeout);
  }, [onDismiss, paused]);

  return (
    <div
      role="status"
      className="fixed bottom-5 right-5 z-50 flex max-w-sm items-center gap-2 rounded-card border border-border bg-surface p-3 text-sm shadow-lg"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <CheckCircle2 className="size-5 text-success" />
      {message}
      {onDismiss && (
        <button className="ml-2 p-1" onClick={onDismiss} aria-label="Dismiss notification">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="flex gap-2 overflow-x-auto">
      {steps.map((step, index) => (
        <li key={step} className="flex min-w-max items-center gap-2 text-xs">
          <span
            className={cn(
              "grid size-6 place-items-center rounded-full border",
              index <= current
                ? "border-primary bg-primary text-white"
                : "border-border text-muted",
            )}
          >
            {index + 1}
          </span>
          <span className={index === current ? "font-semibold" : "text-muted"}>{step}</span>
          {index < steps.length - 1 && <span className="h-px w-5 bg-border" />}
        </li>
      ))}
    </ol>
  );
}
