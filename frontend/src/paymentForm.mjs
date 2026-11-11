const CHANNELS = new Set(["cash", "bank_transfer"]);
const PURPOSES = new Set(["savings", "shares", "dues", "loan_repayment"]);

export function canRecordPayments(roles) {
  return roles.includes("officer") || roles.includes("treasurer");
}

export function nairaToKobo(value) {
  if (typeof value !== "string") {
    throw new TypeError("Enter an amount in naira.");
  }
  const normalized = value.trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) {
    throw new RangeError("Enter an amount with no more than two decimal places.");
  }
  const [nairaText, koboText = ""] = normalized.split(".");
  const kobo = BigInt(nairaText) * 100n + BigInt(koboText.padEnd(2, "0"));
  if (kobo <= 0n || kobo > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new RangeError("Enter an amount greater than zero within the supported limit.");
  }
  return Number(kobo);
}

export function formatKoboAsNaira(kobo) {
  if (!Number.isSafeInteger(kobo) || kobo < 0) {
    throw new RangeError("Kobo amount must be a non-negative safe integer.");
  }
  const amount = BigInt(kobo);
  const naira = amount / 100n;
  const cents = String(amount % 100n).padStart(2, "0");
  return `₦${naira.toLocaleString("en-NG")}.${cents}`;
}

export function validatePaymentDraft(draft) {
  if (!canRecordPayments(draft.roles)) return "Only an officer or treasurer can record a payment.";
  if (!draft.memberId) return "Choose a member.";
  if (!CHANNELS.has(draft.channel)) return "Choose cash or bank transfer.";
  if (!PURPOSES.has(draft.purpose)) return "Choose a payment purpose.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.receivedOn)) return "Choose a valid date received.";
  try {
    nairaToKobo(draft.amountNaira);
  } catch (error) {
    return error instanceof Error ? error.message : "Enter a valid amount.";
  }
  if (draft.channel === "bank_transfer" && !draft.providerReference.trim()) {
    return "Enter the bank reference for this transfer.";
  }
  if (draft.purpose === "loan_repayment") {
    if (!draft.loanId) return "Choose a loan for this repayment.";
    const manualException = draft.loanStatus === "disputed" || draft.deductionsPaused;
    if (manualException && !draft.roles.includes("treasurer")) {
      return "Only a treasurer can record a disputed or paused loan repayment.";
    }
    if (manualException && !draft.reason.trim()) {
      return "Enter a reason for the disputed or paused loan repayment.";
    }
  }
  return null;
}

export function buildPaymentPayload(draft) {
  const manualException =
    draft.purpose === "loan_repayment" &&
    (draft.loanStatus === "disputed" || draft.deductionsPaused);
  return {
    member_id: draft.memberId,
    channel: draft.channel,
    purpose: draft.purpose,
    loan_id: draft.purpose === "loan_repayment" ? draft.loanId : null,
    amount_kobo: nairaToKobo(draft.amountNaira),
    provider_reference: draft.channel === "bank_transfer" ? draft.providerReference.trim() : null,
    received_on: draft.receivedOn,
    fee_kobo: 0,
    fee_bearer: "member",
    reason: manualException ? draft.reason.trim() : null,
  };
}
