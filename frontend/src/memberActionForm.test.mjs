import assert from "node:assert/strict";
import test from "node:test";
import { createAttemptKeyState, getAttemptKey, completeAttempt } from "./memberActionForm.mjs";
import { formatDateReceived, formatRecordedAt } from "./memberActionDates.mjs";

test("a failed form attempt reuses its idempotency key and success resets it", () => {
  const state = createAttemptKeyState();
  let calls = 0;
  const nextKey = () => `key-${++calls}`;
  const first = getAttemptKey(state, "contribution", nextKey);
  assert.equal(getAttemptKey(state, "contribution", nextKey), first);
  assert.equal(calls, 1);
  // A failed request deliberately does not complete/reset the attempt.
  assert.equal(getAttemptKey(state, "contribution", nextKey), "key-1");
  completeAttempt(state, "contribution");
  assert.equal(getAttemptKey(state, "contribution", nextKey), "key-2");
});

test("date received is a calendar date without a clock, even near WAT midnight", () => {
  assert.equal(formatDateReceived("2026-11-08"), "08 Nov 2026");
  assert.equal(formatDateReceived("2026-11-07T23:30:00Z"), "08 Nov 2026");
});

test("recorded timestamp is displayed in WAT with the time", () => {
  assert.match(formatRecordedAt("2026-11-07T23:30:00Z"), /08 Nov 2026/);
  assert.match(formatRecordedAt("2026-11-07T23:30:00Z"), /00:30/);
  assert.equal(formatRecordedAt(null), "—");
});
