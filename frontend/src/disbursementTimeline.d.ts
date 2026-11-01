import type { DisbursementRecord } from "./api/schema";
import type { TimelineEvent } from "./ui/Timeline";
export function eventsForDisbursement(item: DisbursementRecord): TimelineEvent[];
