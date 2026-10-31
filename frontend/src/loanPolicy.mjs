export const loanReadRoles = ["admin", "officer", "treasurer", "committee", "auditor"];

export function canReadLoans(roles) {
  return roles.some((role) => loanReadRoles.includes(role));
}

export function canSubmitLoans(roles) {
  return roles.includes("admin") || roles.includes("officer");
}

export function canDecideLoans(roles, status) {
  return roles.includes("committee") && status === "under_review";
}

export function canSeeDestinationAccount(roles) {
  return roles.some((role) => ["admin", "officer", "treasurer"].includes(role));
}
