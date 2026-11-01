export function canPrepareDisbursement(roles) {
  return roles.includes("treasurer");
}
export function canAuthorizeDisbursement(roles, userId, item) {
  return (
    roles.includes("admin") &&
    item.status === "pending_authorization" &&
    item.prepared_by !== userId
  );
}
export function shouldExplainPreparedByCurrentUser(userId, item) {
  return item.status === "pending_authorization" && item.prepared_by === userId;
}
export function canRecordDisbursementTransfer(roles, userId, item) {
  return (
    roles.includes("treasurer") && item.status === "authorized" && item.authorized_by !== userId
  );
}
export function canConfirmDisbursementPaid(roles, userId, item) {
  return (
    roles.includes("admin") && item.status === "transfer_recorded" && item.authorized_by === userId
  );
}
export function canFailDisbursement(roles, userId, item) {
  return (
    roles.includes("treasurer") &&
    ["authorized", "transfer_recorded"].includes(item.status) &&
    item.authorized_by !== userId
  );
}
export function canReverseDisbursement(roles, userId, item) {
  return (
    roles.some((role) => ["admin", "committee"].includes(role)) &&
    item.status === "paid" &&
    item.confirmed_by !== userId
  );
}
