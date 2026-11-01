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

export const payrollSteps = ["Created", "Dry-run", "Imported", "Posted", "Signed off"];

export function payrollStepIndex(status, dryRunShown = false) {
  if (status === "signed_off") return 4;
  if (status === "posted") return 3;
  if (status === "imported") return 2;
  return dryRunShown ? 1 : 0;
}

export function payrollRunActions(roles, userId, run, dryRunMatches = false) {
  const canWorkWithFiles = roles.includes("officer") || roles.includes("treasurer");
  const fileStatus = run.status === "draft" || run.status === "exported";
  const posterId = run.posted_by ?? run.imported_by ?? null;
  const isAdmin = roles.includes("admin");
  const isPosted = run.status === "posted";

  return {
    canExportExpected: canWorkWithFiles && fileStatus,
    canDryRun: canWorkWithFiles && fileStatus,
    canImport: canWorkWithFiles && fileStatus && dryRunMatches,
    canPost: roles.includes("treasurer") && !isAdmin && run.status === "imported",
    canSignOff: isAdmin && isPosted && posterId !== null && posterId !== userId,
    signOffBlockedBySelf: isAdmin && isPosted && posterId === userId,
    signOffBlockedMissingPoster: isAdmin && isPosted && posterId === null,
  };
}
