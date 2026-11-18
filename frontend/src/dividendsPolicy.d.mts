export function canPrepareDividendRun(roles: string[]): boolean;
export function canApproveDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function canRejectDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function canCancelDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function memberDisplayLabel(
  member: { full_name?: string | null } | null | undefined,
): string;
export function prepareDividendRequestBody(args: {
  recordDate: string;
  meetingOrResolution: string | null | undefined;
  poolNaira: number;
  financialYear: number;
}): { record_date: string; meeting_or_resolution: string | null; pool_kobo: number; financial_year: number };
export function rejectDividendRequestBody(reason: string): { reason: string };
export function looksLikeUuid(value: string): boolean;
