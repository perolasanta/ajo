export const payrollReadRoles: string[];
export function canViewPayroll(roles?: string[]): boolean;
export function canCreatePayrollRun(roles?: string[]): boolean;
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
export const maxPayrollUploadBytes: number;
export type PayrollFileMetadata = { name: string; type: string; size: number };
export function validatePayrollFile(file?: PayrollFileMetadata | null): string | null;
export function uploadPayrollFile<TFile extends PayrollFileMetadata, TResult>(
  file: TFile,
  send: (file: TFile) => Promise<TResult>,
): Promise<TResult>;
export function payrollFileSha256(file: Blob): Promise<string>;
export function nairaInputToKobo(value: string): number | null;
export function payrollSignOffReady(
  fileTotalKobo: number | null,
  bankTotalKobo: number | null,
  exceptions?: Array<{ classification: string; resolution_note: string | null }>,
): boolean;
