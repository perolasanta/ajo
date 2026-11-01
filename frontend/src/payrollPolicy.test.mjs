import assert from "node:assert/strict";
import test from "node:test";
import {
  canViewPayroll,
  canCreatePayrollRun,
  payrollListState,
  payrollReadRoles,
  payrollRunActions,
  payrollStepIndex,
  maxPayrollUploadBytes,
  uploadPayrollFile,
  validatePayrollFile,
} from "./payrollPolicy.mjs";

for (const role of ["officer", "treasurer", "admin"]) {
  test(`${role} can view payroll`, () => {
    assert.equal(canViewPayroll([role]), true);
  });
}

for (const role of ["committee", "auditor", "member"]) {
  test(`${role} cannot view payroll`, () => {
    assert.equal(canViewPayroll([role]), false);
  });
}

test("payroll list access is limited to the decision 1b read roles", () => {
  assert.deepEqual(payrollReadRoles, ["officer", "treasurer", "admin"]);
});

test("only officers and treasurers can create payroll runs", () => {
  assert.equal(canCreatePayrollRun(["officer"]), true);
  assert.equal(canCreatePayrollRun(["treasurer"]), true);
  assert.equal(canCreatePayrollRun(["admin"]), false);
  assert.equal(canCreatePayrollRun(["committee"]), false);
});

test("payroll list exposes loading and error states", () => {
  assert.equal(payrollListState({ isPending: true, isError: false }), "loading");
  assert.equal(payrollListState({ isPending: false, isError: true }), "error");
  assert.equal(payrollListState({ isPending: false, isError: false }), "loading");
});

test("payroll list distinguishes empty and populated results", () => {
  assert.equal(
    payrollListState({ isPending: false, isError: false, data: { items: [] } }),
    "empty",
  );
  assert.equal(
    payrollListState({ isPending: false, isError: false, data: { items: [{ run_id: "r1" }] } }),
    "results",
  );
});

test("payroll stepper follows the persisted state and the session-only dry-run step", () => {
  assert.equal(payrollStepIndex("draft"), 0);
  assert.equal(payrollStepIndex("exported"), 0);
  assert.equal(payrollStepIndex("draft", true), 1);
  assert.equal(payrollStepIndex("imported"), 2);
  assert.equal(payrollStepIndex("posted"), 3);
  assert.equal(payrollStepIndex("signed_off"), 4);
});

test("payroll actions respect role, run state, dry-run match, and sign-off separation", () => {
  const draft = { status: "draft", posted_by: null, imported_by: null };
  const officer = payrollRunActions(["officer"], "officer-1", draft, false);
  assert.equal(officer.canExportExpected, true);
  assert.equal(officer.canDryRun, true);
  assert.equal(officer.canImport, false);
  assert.equal(payrollRunActions(["officer"], "officer-1", draft, true).canImport, true);

  const imported = { status: "imported", posted_by: null, imported_by: "officer-1" };
  assert.equal(payrollRunActions(["treasurer"], "treasurer-1", imported).canPost, true);
  assert.equal(payrollRunActions(["admin"], "admin-1", imported).canPost, false);

  const posted = { status: "posted", posted_by: "treasurer-1", imported_by: "officer-1" };
  assert.equal(payrollRunActions(["admin"], "admin-2", posted).canSignOff, true);
  const poster = payrollRunActions(["admin"], "treasurer-1", posted);
  assert.equal(poster.canSignOff, false);
  assert.equal(poster.signOffBlockedBySelf, true);
  const legacyImporter = payrollRunActions(["admin"], "officer-1", {
    status: "posted",
    posted_by: null,
    imported_by: "officer-1",
  });
  assert.equal(legacyImporter.canSignOff, false);
  assert.equal(legacyImporter.signOffBlockedBySelf, true);
});

test("payroll file validation accepts just under 5 MiB and rejects just over or non-CSV files", () => {
  const valid = { name: "payroll.csv", type: "text/csv", size: maxPayrollUploadBytes - 1 };
  assert.equal(validatePayrollFile(valid), null);
  assert.equal(
    validatePayrollFile({ ...valid, size: maxPayrollUploadBytes + 1 }),
    "Payroll files must be 5 MiB (5 × 1024 × 1024 bytes) or smaller.",
  );
  assert.match(validatePayrollFile({ ...valid, name: "payroll.xlsx" }), /\.csv/);
  assert.match(validatePayrollFile({ ...valid, type: "application/vnd.ms-excel" }), /text\/csv/);
});

test("payroll upload sends a valid CSV and surfaces a server API error", async () => {
  const file = { name: "payroll.csv", type: "text/csv", size: 1024 };
  let sent = false;
  const result = await uploadPayrollFile(file, async (candidate) => {
    sent = candidate === file;
    return { file_sha256: "a".repeat(64) };
  });
  assert.equal(sent, true);
  assert.equal(result.file_sha256, "a".repeat(64));

  await assert.rejects(
    uploadPayrollFile({ ...file, type: "text/plain" }, async () => {
      throw new Error("should not send an invalid file");
    }),
    /text\/csv/,
  );
  await assert.rejects(
    uploadPayrollFile(file, async () => {
      throw new Error("Payroll upload exceeds the 5 MiB limit.");
    }),
    /5 MiB/,
  );
});
