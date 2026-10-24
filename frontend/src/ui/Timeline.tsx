import { Copy } from "lucide-react";
export type TimelineEvent = {
  title: string;
  actor: string;
  role: string;
  time: string;
  channel: string;
  evidence?: string;
};
export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <ol className="border-l border-border pl-5">
      {events.map((event) => (
        <li key={`${event.title}-${event.time}`} className="relative pb-6 last:pb-0">
          <span className="absolute -left-[25px] top-1 size-3 rounded-full border-2 border-surface bg-primary" />
          <p className="font-medium">{event.title}</p>
          <p className="mt-1 text-xs text-muted">
            {event.actor} · {event.role} · {event.time} · {event.channel}
          </p>
          {event.evidence && (
            <button
              type="button"
              className="mt-2 inline-flex items-center gap-1 text-xs text-primary"
              onClick={() => void navigator.clipboard?.writeText(event.evidence!)}
            >
              <Copy className="size-3" />
              Evidence: {event.evidence}
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}
