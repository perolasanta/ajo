const contributionAndRequestRoles = new Set(["officer", "treasurer"]);

/** @param {readonly string[]} roles */
export function canRecordContribution(roles) {
  return roles.some((role) => contributionAndRequestRoles.has(role));
}

/** @param {readonly string[]} roles */
export function canRequestWithdrawal(roles) {
  return roles.some((role) => contributionAndRequestRoles.has(role));
}

/** @param {readonly string[]} roles */
export function canApproveWithdrawal(roles) {
  return roles.includes("treasurer");
}

/** @param {readonly string[]} roles */
export function canSeeMemberFinancialActions(roles) {
  return (
    canRecordContribution(roles) || canRequestWithdrawal(roles) || canApproveWithdrawal(roles)
  );
}
