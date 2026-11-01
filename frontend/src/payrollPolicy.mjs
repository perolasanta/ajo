export const payrollReadRoles = ["officer", "treasurer", "admin"];
export const maxPayrollUploadBytes = 5 * 1024 * 1024;

export function canViewPayroll(roles = []) {
  return payrollReadRoles.some((role) => roles.includes(role));
}

export function canCreatePayrollRun(roles = []) {
  return roles.includes("officer") || roles.includes("treasurer");
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

export function validatePayrollFile(file) {
  if (!file?.name?.toLowerCase().endsWith(".csv")) {
    return "Choose a payroll file with the .csv extension.";
  }
  const mediaType = (file.type ?? "").split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "text/csv") {
    return "The selected file must have the CSV content type (text/csv).";
  }
  if (file.size > maxPayrollUploadBytes) {
    return "Payroll files must be 5 MiB (5 × 1024 × 1024 bytes) or smaller.";
  }
  return null;
}

export async function uploadPayrollFile(file, send) {
  const validationError = validatePayrollFile(file);
  if (validationError) throw new Error(validationError);
  return send(file);
}

export async function payrollFileSha256(file) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("This browser cannot verify payroll file hashes. Try a current browser.");
  }
  const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
