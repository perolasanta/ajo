/** Money is integer kobo everywhere. Format only at the edge. */
export function formatNaira(kobo: number): string {
  const sign = kobo < 0 ? "-" : "";
  const abs = Math.abs(kobo);
  const whole = Math.trunc(abs / 100).toLocaleString("en-NG");
  const frac = String(abs % 100).padStart(2, "0");
  return `${sign}₦${whole}.${frac}`;
}
