import test from "node:test";
import assert from "node:assert/strict";
import {
  bankStatementImportErrorMessage,
  formatBankStatementImportNotice,
  openBankStatementPicker,
  parseBankStatementHeaders,
  suggestBankStatementMapping,
} from "./bankStatementsUpload.mjs";

test("bank statement upload input opens and resets the chosen value", () => {
  let clickCount = 0;
  const input = {
    value: "existing-file.csv",
    click() {
      clickCount += 1;
    },
  };
  assert.equal(openBankStatementPicker(input), true);
  assert.equal(input.value, "");
  assert.equal(clickCount, 1);
});

test("missing upload input is ignored without throwing", () => {
  assert.equal(openBankStatementPicker(null), false);
});

test("CSV headers handle a BOM, quoted commas, and escaped quotes", () => {
  const headers = parseBankStatementHeaders('\uFEFFDate,"Narration, details","Bank ""ref"""\r\n');
  assert.equal(headers[0], "Date");
  assert.equal(headers[1], "Narration, details");
  assert.equal(headers[2], 'Bank "ref"');
});

test("column suggestions map signed amount or separate credit and debit fields", () => {
  const signed = suggestBankStatementMapping(["Value Date", "Description", "Amount", "Reference"]);
  assert.equal(signed.date, "Value Date");
  assert.equal(signed.narration, "Description");
  assert.equal(signed.amount, "Amount");
  assert.equal(signed.credit, "");
  const split = suggestBankStatementMapping(["Date", "Narration", "Credit", "Debit"]);
  assert.equal(split.credit, "Credit");
  assert.equal(split.debit, "Debit");
});

test("import summary reports added, duplicate, and invalid row counts with row numbers", () => {
  assert.equal(
    formatBankStatementImportNotice({
      inserted_count: 2,
      skipped_count: 1,
      errors: [
        { row: 5, error: "narration is required" },
        { row: 8, error: "invalid date" },
      ],
    }),
    "2 rows added; 1 duplicate row skipped; 2 bad rows (CSV rows 5, 8).",
  );
});

test("generic import errors become actionable and retain the request reference", () => {
  assert.equal(
    bankStatementImportErrorMessage("Request failed. Please try again.", "req-123"),
    "Bank statement import could not be completed. Review the CSV mapping and try again. Reference: req-123.",
  );
  assert.equal(
    bankStatementImportErrorMessage("CSV column mapping is invalid.", undefined),
    "CSV column mapping is invalid.",
  );
});
