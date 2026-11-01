export const payrollReadRoles = ["officer", "treasurer", "admin"];

export function canViewPayroll(roles = []) {
  return payrollReadRoles.some((role) => roles.includes(role));
}

export function payrollListState({ isPending, isError, data }) {
  if (isPending) return "loading";
  if (isError) return "error";
  if (!data) return "loading";
  return data.items.length === 0 ? "empty" : "results";
}
