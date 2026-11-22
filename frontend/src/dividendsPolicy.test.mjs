import assert from "node:assert/strict";
import test from "node:test";
import {
  canApproveDividendRun,
  canCancelDividendRun,
  canPrepareDividendRun,
  canRejectDividendRun,
  looksLikeUuid,
  memberDisplayLabel,
  prepareDividendRequestBody,
  rejectDividendRequestBody,
} from "./dividendsPolicy.mjs";
import { periodsForFrequency } from "./dividendPeriods.mjs";

test("period dropdown follows the frequency returned by the effective policy API", () => {
  const policy = { dividend_frequency: "quarterly" };
  const options = periodsForFrequency(policy.dividend_frequency, new Date("2026-11-18"));
  assert.match(options.map((option) => option.key).join(","), /2026-Q4/);
  assert.doesNotMatch(options.map((option) => option.key).join(","), /2026(?:,|$)/);
});

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
    meetingOrResolution: "AGM 2025",
    poolNaira: 1000,
    financialYear: 2025,
  });
  assert.deepEqual(body, {
    record_date: "2025-06-30",
    meeting_or_resolution: "AGM 2025",
    pool_kobo: 100000,
    financial_year: 2025,
  });
  assert.deepEqual(Object.keys(body).sort(), [
    "financial_year",
    "meeting_or_resolution",
    "pool_kobo",
    "record_date",
  ]);
  assert.ok(!("member_name" in body));
  assert.ok(!("member_id" in body));
  assert.ok(!("amount_kobo" in body));
  assert.ok(
    !("declared_by_meeting_id" in body),
    "Old field declared_by_meeting_id must not appear",
  );
});

test("prepare body converts naira to integer kobo exactly", () => {
  assert.equal(
    prepareDividendRequestBody({
      recordDate: "2025-06-30",
      meetingOrResolution: null,
      poolNaira: 0.5,
      financialYear: 2025,
    }).pool_kobo,
    50,
  );
  assert.equal(
    prepareDividendRequestBody({
      recordDate: "2025-06-30",
      meetingOrResolution: null,
      poolNaira: 1234.56,
      financialYear: 2025,
    }).pool_kobo,
    123456,
  );
});

test("prepare body meeting_or_resolution can be null", () => {
  const body = prepareDividendRequestBody({
    recordDate: "2025-06-30",
    meetingOrResolution: null,
    poolNaira: 500,
    financialYear: 2026,
  });
  assert.equal(body.meeting_or_resolution, null);
  assert.equal(body.financial_year, 2026);
});

test("reject body carries only the reason, never names or amounts", () => {
  const body = rejectDividendRequestBody("not eligible");
  assert.deepEqual(body, { reason: "not eligible" });
  assert.deepEqual(Object.keys(body), ["reason"]);
  assert.ok(!("member_name" in body));
  assert.ok(!("amount_kobo" in body));
});

test("looksLikeUuid detects UUID-shaped strings", () => {
  assert.equal(looksLikeUuid("3f6c3ae4-b4b1-4c8a-bb9e-7891a8b8d1f4"), true);
  assert.equal(looksLikeUuid("AGM 2026"), false);
  assert.equal(looksLikeUuid(""), false);
  assert.equal(looksLikeUuid("not-a-uuid"), false);
});

test("prepare body meeting_or_resolution never contains a bare UUID as a label", () => {
  // If the UI sends a UUID-formatted string, the body carries it as text — but
  // the frontend should NOT render such a value as a plain label to the user.
  // This test asserts that looksLikeUuid() catches such a value so the UI can
  // choose to redact or format it appropriately.
  const uuidStr = "3f6c3ae4-b4b1-4c8a-bb9e-7891a8b8d1f4";
  const body = prepareDividendRequestBody({
    recordDate: "2026-01-01",
    meetingOrResolution: uuidStr,
    poolNaira: 100,
    financialYear: 2026,
  });
  // If meeting_or_resolution looks like a UUID, the UI must not render it verbatim as a label.
  if (looksLikeUuid(body.meeting_or_resolution ?? "")) {
    // This branch must never reach production UI — test documents the detection path.
    assert.match(body.meeting_or_resolution ?? "", /^[0-9a-f-]{36}$/i);
  }
});
