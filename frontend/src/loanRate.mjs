export function formatRate(annualRateBps) {
  const annual = annualRateBps / 100;
  const monthly = annualRateBps / 1200;
  return `${annual.toFixed(2)}% a year (${monthly.toFixed(2)}% a month)`;
}
