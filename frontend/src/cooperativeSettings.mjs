function scaledInteger(value, places, label) {
  const text = String(value).trim();
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) throw new Error(`${label} must be a non-negative number`);

  const scale = 10n ** BigInt(places);
  const whole = BigInt(match[1]);
  const fraction = match[2] ?? "";
  const kept = fraction.slice(0, places).padEnd(places, "0");
  const discarded = fraction.slice(places);
  let result = whole * scale + BigInt(kept || "0");
  if (discarded.length > 0 && discarded[0] >= "5") result += 1n;

  if (result > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error(`${label} is too large`);
  }
  return Number(result);
}

export function amountNairaToKobo(value) {
  return scaledInteger(value, 2, "Amount");
}

export function roundNairaToKobo(value) {
  return amountNairaToKobo(value);
}

export function loanMultipleToX100(value) {
  return scaledInteger(value, 2, "Loan-to-savings multiple");
}

export function annualPercentToBps(value) {
  return scaledInteger(value, 2, "Annual percentage");
}

export function monthlyPercentToAnnualBps(value) {
  const monthlyBps = annualPercentToBps(value);
  if (monthlyBps > Math.floor(Number.MAX_SAFE_INTEGER / 12)) {
    throw new Error("Annual percentage is too large");
  }
  return monthlyBps * 12;
}
