import { cn } from "../lib";

const palette = [
  "bg-[#DCE9E4] text-[#0E2F2B]",
  "bg-[#E9E1D0] text-[#554018]",
  "bg-[#DFE7EF] text-[#1D4E70]",
  "bg-[#EADDE5] text-[#70405C]",
  "bg-[#E2E5D5] text-[#46562A]",
  "bg-[#E9DFD7] text-[#70452D]",
];

function hashName(name: string) {
  return Array.from(name).reduce(
    (hash, character) => (hash * 31 + character.charCodeAt(0)) >>> 0,
    0,
  );
}

export function initials(name: string) {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-full text-[11px] font-semibold",
        palette[hashName(name) % palette.length],
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}
