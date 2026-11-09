import assert from "node:assert/strict";
import test from "node:test";
import {
  PASSWORD_CHANGED_NOTICE,
  PASSWORD_NOTICE_DURATION_MS,
  passwordNoticeAfterEdit,
} from "./passwordNotice.js";

test("password change success notice uses the required message", () => {
  assert.equal(
    PASSWORD_CHANGED_NOTICE,
    "Password changed. Your other sessions were signed out.",
  );
});

test("notice remains through an early edit and expires at ten seconds", () => {
  const shownAt = 1_000;
  assert.equal(passwordNoticeAfterEdit(shownAt, shownAt + 1), shownAt);
  assert.equal(
    passwordNoticeAfterEdit(shownAt, shownAt + PASSWORD_NOTICE_DURATION_MS),
    null,
  );
});

test("an edit after expiry clears the notice", () => {
  assert.equal(passwordNoticeAfterEdit(1_000, 11_001), null);
  assert.equal(passwordNoticeAfterEdit(null, 11_001), null);
});
