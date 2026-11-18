export function openBankStatementPicker(input) {
  if (!input) return false;
  input.value = "";
  input.click();
  return true;
}

export function parseBankStatementHeaders(csvText) {
  const source = String(csvText).replace(/^\uFEFF/, "");
  const headers = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '"') {
      if (quoted && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      headers.push(field.trim());
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      break;
    } else {
      field += character;
    }
  }
  headers.push(field.trim());
  return headers;
}

export function suggestBankStatementMapping(headers) {
  const find = (aliases) => {
    const normalizedAliases = new Set(aliases);
    return (
      headers.find((header) =>
        normalizedAliases.has(
          String(header)
            .toLowerCase()
            .replace(/[^a-z0-9]/g, ""),
        ),
      ) ?? ""
    );
  };
  const amount = find(["amount", "signedamount", "transactionamount"]);
  return {
    date: find(["date", "transactiondate", "valuedate", "posteddate"]),
    narration: find(["narration", "description", "details", "particulars", "memo"]),
    amount,
    credit: amount ? "" : find(["credit", "creditamount", "deposit", "moneyin"]),
    debit: amount ? "" : find(["debit", "debitamount", "withdrawal", "moneyout"]),
    reference: find(["reference", "ref", "transactionreference", "bankreference"]),
  };
}

export function formatBankStatementImportNotice(result) {
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const rowNumbers = errors.map(({ row }) => row).filter(Number.isInteger);
  const addedLabel = result.inserted_count === 1 ? "row" : "rows";
  const skippedLabel = result.skipped_count === 1 ? "duplicate row" : "duplicate rows";
  const badLabel = errors.length === 1 ? "bad row" : "bad rows";
  const badRows = errors.length
    ? `${errors.length} ${badLabel} (CSV rows ${rowNumbers.join(", ")})`
    : "0 bad rows";
  return `${result.inserted_count} ${addedLabel} added; ${result.skipped_count} ${skippedLabel} skipped; ${badRows}.`;
}

export function bankStatementImportErrorMessage(message, requestId) {
  if (typeof message === "string" && message && message !== "Request failed. Please try again.") {
    return message;
  }
  const fallback =
    "Bank statement import could not be completed. Review the CSV mapping and try again.";
  return requestId ? `${fallback} Reference: ${requestId}.` : fallback;
}
