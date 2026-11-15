export const noEligibleConfirmerWarning =
  "No other eligible person can confirm this. Add another officer or treasurer.";

export function pendingNoConfirmerSummary(count) {
  if (!Number.isInteger(count) || count < 1) return "";
  return `${count} pending ${count === 1 ? "item has" : "items have"} no eligible confirmer.`;
}
