import { formatNaira } from "../lib";
import { Tooltip } from "./Overlay";
export function Money({ kobo, className = "" }: { kobo: number; className?: string }) {
  return <span className={`tabular-nums ${className}`}>{formatNaira(kobo)}</span>;
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
