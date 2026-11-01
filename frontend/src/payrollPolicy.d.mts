export const payrollReadRoles: string[];
export function canViewPayroll(roles?: string[]): boolean;
export function payrollListState(query: {
  isPending: boolean;
  isError: boolean;
  data?: { items: unknown[] };
}): "loading" | "error" | "empty" | "results";
