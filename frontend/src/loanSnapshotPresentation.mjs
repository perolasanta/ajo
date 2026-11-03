function integerValue(value) {
  if (typeof value === "number" && Number.isSafeInteger(value)) return BigInt(value);
  if (typeof value === "string" && /^-?\d+$/.test(value)) return BigInt(value);
  return null;
}

function formatNaira(koboValue) {
  const kobo = integerValue(koboValue);
  if (kobo === null || kobo < 0n) return "Unavailable";
  const naira = kobo / 100n;
  const remainder = (kobo % 100n).toString().padStart(2, "0");
  return `₦${naira.toLocaleString("en-NG")}.${remainder}`;
}

function formatRate(bpsValue) {
  const bps = integerValue(bpsValue);
  if (bps === null || bps < 0n) return "Unavailable";
  const whole = bps / 100n;
  const hundredths = (bps % 100n).toString().padStart(2, "0");
  return `${whole}.${hundredths}%`;
}

function formatTenor(value) {
  const months = integerValue(value);
  if (months === null || months <= 0n) return "Unavailable";
  return `${months} months`;
}

function destinationLabel(destination) {
  if (typeof destination !== "object" || destination === null) return "Not provided";
  const record = destination;
  if (
    typeof record.bank_code !== "string" ||
    typeof record.last4 !== "string" ||
    !/^\d{4}$/.test(record.last4)
  ) {
    return "Not provided";
  }
  const accountName =
    typeof record.account_name === "string" && record.account_name.trim()
      ? `${record.account_name.trim()} · `
      : "";
  const verification = record.verified === true ? "verified" : "not verified";
  return `${accountName}bank ${record.bank_code} · ••${record.last4} (${verification})`;
}

export function loanSnapshotPresentation(
  snapshot,
  { productName, beneficiaryName, showDestination = false },
) {
  const terms = typeof snapshot === "object" && snapshot !== null ? snapshot : {};
  return [
    { label: "Principal", value: formatNaira(terms.principal_kobo) },
    { label: "Annual rate", value: formatRate(terms.annual_rate_bps) },
    { label: "Tenor", value: formatTenor(terms.tenor_months) },
    { label: "Product name", value: productName || "Unavailable" },
    { label: "Beneficiary name", value: beneficiaryName || "Unavailable" },
    {
      label: "Destination",
      value: showDestination ? destinationLabel(terms.destination) : "Not displayed",
    },
  ];
}
