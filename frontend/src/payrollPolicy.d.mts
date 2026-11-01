export const payrollReadRoles: string[];
export function canViewPayroll(roles?: string[]): boolean;
export function payrollListState(query: {
  isPending: boolean;
  isError: boolean;
  data?: { items: unknown[] };
}): "loading" | "error" | "empty" | "results";
export const payrollSteps: string[];
export type PayrollRunAccessState = {
  status: string;
  posted_by: string | null;
  imported_by: string | null;
};
export function payrollStepIndex(status: string, dryRunShown?: boolean): number;
export function payrollRunActions(
  roles: string[],
  userId: string,
  run: PayrollRunAccessState,
  dryRunMatches?: boolean,
): {
  canExportExpected: boolean;
  canDryRun: boolean;
  canImport: boolean;
  canPost: boolean;
  canSignOff: boolean;
  signOffBlockedBySelf: boolean;
  signOffBlockedMissingPoster: boolean;
};
