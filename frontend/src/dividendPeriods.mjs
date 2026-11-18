/**
 * Dividend period helpers for Unit 3: configurable dividend frequency.
 *
 * All functions are pure and date-driven so they can be unit-tested with
 * `node --test` without any React or network calls.
 *
 * Period key format:
 *   annual      → "2026"
 *   half_yearly → "2026-H1" | "2026-H2"
 *   quarterly   → "2026-Q1" | "2026-Q2" | "2026-Q3" | "2026-Q4"
 *   monthly     → "2026-M01" … "2026-M12"
 */

/** @typedef {"annual"|"half_yearly"|"quarterly"|"monthly"} DividendFrequency */

/**
 * Returns the period boundaries (start and end Date, UTC midnight) for a
 * given period key string.
 * @param {string} key
 * @returns {{ start: Date, end: Date }}
 */
export function periodBoundaries(key) {
  // Annual: "2026"
  if (/^\d{4}$/.test(key)) {
    const y = Number(key);
    return { start: utcDate(y, 1, 1), end: utcDate(y, 12, 31) };
  }
  // Half-yearly: "2026-H1" or "2026-H2"
  const hm = key.match(/^(\d{4})-H([12])$/);
  if (hm) {
    const y = Number(hm[1]);
    const h = Number(hm[2]);
    if (h === 1) return { start: utcDate(y, 1, 1), end: utcDate(y, 6, 30) };
    return { start: utcDate(y, 7, 1), end: utcDate(y, 12, 31) };
  }
  // Quarterly: "2026-Q1" … "2026-Q4"
  const qm = key.match(/^(\d{4})-Q([1-4])$/);
  if (qm) {
    const y = Number(qm[1]);
    const q = Number(qm[2]);
    const startMonth = (q - 1) * 3 + 1; // 1, 4, 7, 10
    const endMonth = q * 3; // 3, 6, 9, 12
    return { start: utcDate(y, startMonth, 1), end: lastDayOf(y, endMonth) };
  }
  // Monthly: "2026-M11"
  const mm = key.match(/^(\d{4})-M(\d{2})$/);
  if (mm) {
    const y = Number(mm[1]);
    const m = Number(mm[2]);
    return { start: utcDate(y, m, 1), end: lastDayOf(y, m) };
  }
  throw new Error(`Unrecognised period key: ${key}`);
}

/**
 * Returns a human-readable label for a period key.
 * @param {string} key
 * @returns {string}
 */
export function periodLabel(key) {
  if (/^\d{4}$/.test(key)) return key;
  const hm = key.match(/^(\d{4})-H([12])$/);
  if (hm) {
    const year = hm[1];
    const h = hm[2];
    return h === "1" ? `H1 ${year} (Jan to Jun)` : `H2 ${year} (Jul to Dec)`;
  }
  const qm = key.match(/^(\d{4})-Q([1-4])$/);
  if (qm) {
    const year = qm[1];
    const q = Number(qm[2]);
    const months = [
      ["Jan", "Mar"],
      ["Apr", "Jun"],
      ["Jul", "Sep"],
      ["Oct", "Dec"],
    ];
    const [from, to] = months[q - 1];
    return `Q${q} ${year} (${from} to ${to})`;
  }
  const mm = key.match(/^(\d{4})-M(\d{2})$/);
  if (mm) {
    const year = mm[1];
    const month = Number(mm[2]);
    const name = new Date(Date.UTC(Number(year), month - 1, 1)).toLocaleString("en-GB", {
      month: "short",
      timeZone: "UTC",
    });
    return `${name} ${year}`;
  }
  return key;
}

/**
 * Given a frequency and a reference date (defaults to today), returns the
 * period key that the date falls in.
 * @param {DividendFrequency} frequency
 * @param {Date} [refDate]
 * @returns {string}
 */
export function periodKeyForFrequency(frequency, refDate = new Date()) {
  const y = refDate.getUTCFullYear();
  const m = refDate.getUTCMonth() + 1; // 1-based
  if (frequency === "annual") return String(y);
  if (frequency === "half_yearly") return m <= 6 ? `${y}-H1` : `${y}-H2`;
  if (frequency === "quarterly") {
    const q = Math.ceil(m / 3);
    return `${y}-Q${q}`;
  }
  if (frequency === "monthly") return `${y}-M${String(m).padStart(2, "0")}`;
  throw new Error(`Unknown frequency: ${frequency}`);
}

/**
 * Returns all periods that have already started on or before `today`,
 * starting from 2001, most-recent first. Excludes periods whose start
 * date is after today.
 * @param {DividendFrequency} frequency
 * @param {Date} [today]
 * @returns {{ key: string, label: string }[]}
 */
export function periodsForFrequency(frequency, today = new Date()) {
  const todayStr = today.toISOString().slice(0, 10);
  /** @type {{ key: string, label: string }[]} */
  const result = [];
  const currentYear = today.getUTCFullYear();

  for (let y = currentYear; y >= 2001; y--) {
    const keys = periodKeysForYear(frequency, y);
    for (let i = keys.length - 1; i >= 0; i--) {
      const key = keys[i];
      const { start } = periodBoundaries(key);
      if (start.toISOString().slice(0, 10) <= todayStr) {
        result.push({ key, label: periodLabel(key) });
      }
    }
  }
  return result;
}

/**
 * Returns true if `dateStr` (YYYY-MM-DD) falls within the given period key
 * (inclusive on both ends).
 * @param {string} dateStr
 * @param {string} periodKey
 * @returns {boolean}
 */
export function recordDateInPeriod(dateStr, periodKey) {
  const { start, end } = periodBoundaries(periodKey);
  const startStr = start.toISOString().slice(0, 10);
  const endStr = end.toISOString().slice(0, 10);
  return dateStr >= startStr && dateStr <= endStr;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/** @param {number} year @param {number} month @param {number} day */
function utcDate(year, month, day) {
  return new Date(Date.UTC(year, month - 1, day));
}

/** Last day of month as a UTC Date. Handles leap years via Date overflow. */
function lastDayOf(year, month) {
  // Setting day=0 of next month gives last day of this month
  return new Date(Date.UTC(year, month, 0));
}

/**
 * Returns all period keys for a year in ascending order.
 * @param {DividendFrequency} frequency
 * @param {number} year
 * @returns {string[]}
 */
function periodKeysForYear(frequency, year) {
  if (frequency === "annual") return [String(year)];
  if (frequency === "half_yearly") return [`${year}-H1`, `${year}-H2`];
  if (frequency === "quarterly") return [`${year}-Q1`, `${year}-Q2`, `${year}-Q3`, `${year}-Q4`];
  if (frequency === "monthly") {
    const keys = [];
    for (let m = 1; m <= 12; m++) keys.push(`${year}-M${String(m).padStart(2, "0")}`);
    return keys;
  }
  throw new Error(`Unknown frequency: ${frequency}`);
}
