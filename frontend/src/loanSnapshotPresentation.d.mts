export type LoanSnapshotField = { label: string; value: string };

export function loanSnapshotPresentation(
  snapshot: Record<string, unknown> | null,
  names: { productName: string; beneficiaryName: string; showDestination: boolean },
): LoanSnapshotField[];
