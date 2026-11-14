export const SHARE_SAVINGS_ERROR =
  "Requested share purchase amount exceeds the member's available savings";

/**
 * @param {number} sharesCount
 * @param {number | undefined} unitPriceNaira
 * @param {number | undefined} savingsKobo
 */
export function sharePurchaseRequestState(sharesCount, unitPriceNaira, savingsKobo) {
  const amountKobo = sharesCount * unitPriceNaira * 100;
  const ready =
    Number.isSafeInteger(sharesCount) &&
    sharesCount > 0 &&
    Number.isSafeInteger(unitPriceNaira) &&
    unitPriceNaira > 0 &&
    Number.isSafeInteger(savingsKobo) &&
    savingsKobo >= 0 &&
    Number.isSafeInteger(amountKobo);

  return {
    amountKobo: ready ? amountKobo : undefined,
    canSubmit: ready && amountKobo <= savingsKobo,
    overSavings: ready && amountKobo > savingsKobo,
  };
}
