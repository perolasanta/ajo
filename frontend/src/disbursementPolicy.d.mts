import type { DisbursementRecord } from "./api/schema";
export function canPrepareDisbursement(roles: string[]): boolean;
export function canAuthorizeDisbursement(
  roles: string[],
  userId: string,
  item: DisbursementRecord,
): boolean;
export function shouldExplainPreparedByCurrentUser(
  userId: string,
  item: DisbursementRecord,
): boolean;
export function canRecordDisbursementTransfer(
  roles: string[],
  userId: string,
  item: DisbursementRecord,
): boolean;
export function canConfirmDisbursementPaid(
  roles: string[],
  userId: string,
  item: DisbursementRecord,
): boolean;
export function canFailDisbursement(
  roles: string[],
  userId: string,
  item: DisbursementRecord,
): boolean;
export function canReverseDisbursement(
  roles: string[],
  userId: string,
  item: DisbursementRecord,
): boolean;
