/** @param {string} value */
function eventTime(value) {
  return (
    new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Africa/Lagos",
    }).format(new Date(value)) + " WAT"
  );
}

/** @param {string} title @param {string} actor @param {string} value @param {string=} evidence */
function event(title, actor, value, evidence) {
  return {
    title,
    actor,
    role: "Staff",
    time: eventTime(value),
    channel: "Manual transfer record",
    evidence,
  };
}

/**
 * @param {import("./api/schema").DisbursementRecord} item
 */
export function eventsForDisbursement(item) {
  const events = [event("Prepared", `User ${item.prepared_by}`, item.created_at)];
  if (item.authorized_at && item.authorized_by)
    events.push(event("Authorized", `User ${item.authorized_by}`, item.authorized_at));
  if (item.transfer_recorded_at)
    events.push(
      event(
        "Transfer recorded",
        "Treasurer",
        item.transfer_recorded_at,
        item.transfer_reference ?? undefined,
      ),
    );
  if (item.paid_at && item.confirmed_by)
    events.push(event("Paid confirmed", `User ${item.confirmed_by}`, item.paid_at));
  if (item.failed_at)
    events.push(
      event("Transfer failed", "Treasurer", item.failed_at, item.failure_reason ?? undefined),
    );
  if (item.reversed_at)
    events.push(
      event("Payment reversed", "Staff", item.reversed_at, item.reversal_reason ?? undefined),
    );
  return events;
}
