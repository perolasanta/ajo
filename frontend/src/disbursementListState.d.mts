import type { DisbursementPage } from "./api/schema";
export function disbursementListState(query: {
  isPending: boolean;
  isError: boolean;
  data?: DisbursementPage;
}): "loading" | "error" | "empty" | "results";
