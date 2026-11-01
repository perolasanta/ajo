import assert from "node:assert/strict";
import test from "node:test";
import { disbursementListState } from "./disbursementListState.mjs";

test("disbursement list exposes loading and error states", () => {
  assert.equal(disbursementListState({ isPending: true, isError: false }), "loading");
  assert.equal(disbursementListState({ isPending: false, isError: true }), "error");
});

test("disbursement list distinguishes empty and populated results", () => {
  assert.equal(
    disbursementListState({ isPending: false, isError: false, data: { items: [] } }),
    "empty",
  );
  assert.equal(
    disbursementListState({ isPending: false, isError: false, data: { items: [{ id: "d1" }] } }),
    "results",
  );
});
