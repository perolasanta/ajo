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
    return headers.find((header) =>
      normalizedAliases.has(String(header).toLowerCase().replace(/[^a-z0-9]/g, "")),
    ) ?? "";
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
