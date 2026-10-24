import {
  AlertTriangle,
  Banknote,
  Check,
  CircleArrowRight,
  CircleDot,
  Clock,
  Eye,
  Lock,
  Pencil,
  Repeat2,
  TimerOff,
} from "lucide-react";
import type { PropsWithChildren } from "react";
import { cn } from "../lib";

const statuses = {
  draft: { label: "Draft", className: "bg-bg text-muted", Icon: Pencil },
  "awaiting confirmation": {
    label: "Awaiting confirmation",
    className: "bg-warning-soft text-warning",
    Icon: Clock,
  },
  "under review": { label: "Under review", className: "bg-info-soft text-info", Icon: Eye },
  approved: { label: "Approved", className: "bg-success-soft text-success", Icon: Check },
  ready: { label: "Ready", className: "bg-info-soft text-info", Icon: CircleArrowRight },
  disbursed: { label: "Disbursed", className: "bg-primary-soft text-primary", Icon: Banknote },
  repaying: { label: "Repaying", className: "bg-primary-soft text-primary", Icon: Repeat2 },
  closed: { label: "Closed", className: "bg-stone-200 text-stone-700", Icon: Lock },
  disputed: { label: "Disputed", className: "bg-danger-soft text-danger", Icon: AlertTriangle },
  expired: { label: "Expired", className: "bg-bg text-muted", Icon: TimerOff },
} as const;

export function Badge({
  children,
  status,
  className,
}: PropsWithChildren<{ status?: string; className?: string }>) {
  const item = statuses[(status ?? String(children)).toLowerCase() as keyof typeof statuses] ?? {
    label: children,
    className: "bg-bg text-muted",
    Icon: CircleDot,
  };
  const Icon = item.Icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-control px-2 py-1 text-xs font-medium",
        item.className,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {item.label}
    </span>
  );
}
