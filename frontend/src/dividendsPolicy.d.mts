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
  declaredByMeetingId: string;
  poolNaira: number;
}): { record_date: string; declared_by_meeting_id: string; pool_kobo: number };
export function rejectDividendRequestBody(reason: string): { reason: string };
