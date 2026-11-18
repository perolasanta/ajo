export type DividendFrequency = "annual" | "half_yearly" | "quarterly" | "monthly";

export interface PeriodOption {
  key: string;
  label: string;
}

export interface PeriodBoundaries {
  start: Date;
  end: Date;
}

/**
 * Returns the start and end Date (UTC midnight) for a period key string.
 * Supports annual ("2026"), half-yearly ("2026-H1"), quarterly ("2026-Q3"),
 * and monthly ("2026-M11") keys.
 */
export function periodBoundaries(key: string): PeriodBoundaries;

/**
 * Returns a human-readable label for a period key, e.g. "Q3 2026 (Jul to Sep)".
 */
export function periodLabel(key: string): string;

/**
 * Given a dividend frequency and a reference date, returns the period key
 * the date falls in.
 */
export function periodKeyForFrequency(frequency: DividendFrequency, refDate?: Date): string;

/**
 * Returns all period options (key + label) that have started on or before
 * `today`, starting from 2001, most-recent first.
 */
export function periodsForFrequency(frequency: DividendFrequency, today?: Date): PeriodOption[];

/**
 * Returns true if `dateStr` (YYYY-MM-DD) falls within the period key (inclusive).
 */
export function recordDateInPeriod(dateStr: string, periodKey: string): boolean;
