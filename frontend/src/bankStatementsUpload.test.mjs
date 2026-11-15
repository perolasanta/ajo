import test from "node:test";
import assert from "node:assert/strict";

import { openBankStatementPicker } from "./bankStatementsUpload.mjs";

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
