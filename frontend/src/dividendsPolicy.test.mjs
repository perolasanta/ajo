import assert from "node:assert/strict";
import test from "node:test";
import {
  canApproveDividendRun,
  canCancelDividendRun,
  canPrepareDividendRun,
  canRejectDividendRun,
  memberDisplayLabel,
  prepareDividendRequestBody,
  rejectDividendRequestBody,
} from "./dividendsPolicy.mjs";

test("only a treasurer can prepare a dividend run", () => {
  assert.equal(canPrepareDividendRun(["treasurer"]), true);
  assert.equal(canPrepareDividendRun(["admin"]), false);
  assert.equal(canPrepareDividendRun(["officer"]), false);
  assert.equal(canPrepareDividendRun(["auditor"]), false);
  assert.equal(canPrepareDividendRun(["committee"]), false);
});

test("an admin approves a run only while unprocessed and never their own", () => {
  assert.equal(canApproveDividendRun(["admin"], "unprocessed", "prep", "me"), true);
  assert.equal(canApproveDividendRun(["admin"], "unprocessed", "me", "me"), false);
  assert.equal(canApproveDividendRun(["admin"], "approved", "prep", "me"), false);
  assert.equal(canApproveDividendRun(["admin"], "rejected", "prep", "me"), false);
  assert.equal(canApproveDividendRun(["treasurer"], "unprocessed", "prep", "me"), false);
  assert.equal(canApproveDividendRun(["auditor"], "unprocessed", "prep", "me"), false);
  assert.equal(canApproveDividendRun(["committee"], "unprocessed", "prep", "me"), false);
  assert.equal(canApproveDividendRun(["officer"], "unprocessed", "prep", "me"), false);
});

test("reject uses the same gate as approve", () => {
  assert.equal(canRejectDividendRun(["admin"], "unprocessed", "prep", "me"), true);
  assert.equal(canRejectDividendRun(["admin"], "unprocessed", "me", "me"), false);
  assert.equal(canRejectDividendRun(["admin"], "approved", "prep", "me"), false);
});

test("only the preparer can cancel, and only while unprocessed", () => {
  assert.equal(canCancelDividendRun(["treasurer"], "unprocessed", "me", "me"), true);
  assert.equal(canCancelDividendRun(["treasurer"], "unprocessed", "prep", "me"), false);
  assert.equal(canCancelDividendRun(["admin"], "unprocessed", "prep", "me"), false);
  assert.equal(canCancelDividendRun(["treasurer"], "approved", "me", "me"), false);
  assert.equal(canCancelDividendRun(["treasurer"], "unprocessed", null, "me"), false);
});

test("member label is name only with Unnamed/Unknown fallbacks", () => {
  assert.equal(memberDisplayLabel(undefined), "Unknown user");
  assert.equal(memberDisplayLabel(null), "Unknown user");
  assert.equal(memberDisplayLabel({ full_name: "" }), "Unnamed user");
  assert.equal(memberDisplayLabel({ full_name: "   " }), "Unnamed user");
  assert.equal(memberDisplayLabel({ full_name: null }), "Unnamed user");
  assert.equal(memberDisplayLabel({ full_name: "Ada Okafor" }), "Ada Okafor");
});

test("prepare body carries only the API fields and never names or per-member amounts", () => {
  const body = prepareDividendRequestBody({
    recordDate: "2025-06-30",
    declaredByMeetingId: "meeting-uuid",
    poolNaira: 1000,
  });
  assert.deepEqual(body, {
    record_date: "2025-06-30",
    declared_by_meeting_id: "meeting-uuid",
    pool_kobo: 100000,
  });
  assert.deepEqual(Object.keys(body).sort(), [
    "declared_by_meeting_id",
    "pool_kobo",
    "record_date",
  ]);
  assert.ok(!("member_name" in body));
  assert.ok(!("member_id" in body));
  assert.ok(!("amount_kobo" in body));
});

test("prepare body converts naira to integer kobo exactly", () => {
  assert.equal(
    prepareDividendRequestBody({
      recordDate: "2025-06-30",
      declaredByMeetingId: "m",
      poolNaira: 0.5,
    }).pool_kobo,
    50,
  );
  assert.equal(
    prepareDividendRequestBody({
      recordDate: "2025-06-30",
      declaredByMeetingId: "m",
      poolNaira: 1234.56,
    }).pool_kobo,
    123456,
  );
});

test("reject body carries only the reason, never names or amounts", () => {
  const body = rejectDividendRequestBody("not eligible");
  assert.deepEqual(body, { reason: "not eligible" });
  assert.deepEqual(Object.keys(body), ["reason"]);
  assert.ok(!("member_name" in body));
  assert.ok(!("amount_kobo" in body));
});
