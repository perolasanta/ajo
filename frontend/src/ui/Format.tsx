import { formatNaira } from "../lib";
import { Tooltip } from "./Overlay";
import { formatDateReceived, formatRecordedAt } from "../memberActionDates.mjs";
export function Money({ kobo, className = "" }: { kobo: number; className?: string }) {
  return (
    <span
      className={`whitespace-nowrap tabular-nums text-[clamp(0.75rem,3.5vw,1rem)] ${className}`}
    >
      {formatNaira(kobo)}
    </span>
  );
}
export function DateTime({ value }: { value: string | Date }) {
  const date = new Date(value);
  const absolute = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Lagos",
  })
    .format(date)
    .replace(",", "");
  return (
    <Tooltip content={date.toLocaleString()}>
      <time dateTime={date.toISOString()}>{absolute} WAT</time>
    </Tooltip>
  );
}

export function DateReceived({ value }: { value: string }) {
  return <time dateTime={value}>{formatDateReceived(value)}</time>;
}

export function RecordedAt({ value }: { value?: string | null }) {
  if (!value) return <span>—</span>;
  return <time dateTime={value}>{formatRecordedAt(value)}</time>;
}
