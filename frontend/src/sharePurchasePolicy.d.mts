export const SHARE_SAVINGS_ERROR: string;

export type SharePurchaseRequestState = {
  amountKobo: number | undefined;
  canSubmit: boolean;
  overSavings: boolean;
};

export function sharePurchaseRequestState(
  sharesCount: number,
  unitPriceNaira: number | undefined,
  savingsKobo: number | undefined,
): SharePurchaseRequestState;
