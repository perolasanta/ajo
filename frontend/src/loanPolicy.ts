export const loanReadRoles = ["admin", "officer", "treasurer", "committee", "auditor"];

export function canReadLoans(roles: string[]): boolean {
  return roles.some((role) => loanReadRoles.includes(role));
}

export function canSubmitLoans(roles: string[]): boolean {
  return roles.includes("admin") || roles.includes("officer");
}

export function canDecideLoans(roles: string[], status: string): boolean {
  return roles.includes("committee") && status === "under_review";
}

export function canSeeDestinationAccount(roles: string[]): boolean {
  return roles.some((role) => ["admin", "officer", "treasurer"].includes(role));
}
