export type ReviewRow = {
  key: string;
  label: string;
  current: string;
  proposed: string;
  changed: boolean;
};
export function buildPolicyReviewRows(
  current: Record<string, unknown>,
  proposed: Record<string, unknown>,
): ReviewRow[];
export function buildProductReviewRows(
  current: Record<string, unknown>,
  proposed: Record<string, unknown>,
): ReviewRow[];
export function canApproveSettings(
  session: { roles: string[]; userId: string },
  proposerId: string,
): boolean;
export function formatActorLabel(
  name: string | null | undefined,
  email: string | null | undefined,
  showEmail: boolean,
): string;
