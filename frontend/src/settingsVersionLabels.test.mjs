import assert from "node:assert/strict";
import test from "node:test";
import { versionStateLabel } from "./settingsVersionLabels.mjs";

test("labels the version currently in force", () => {
  assert.match(
    versionStateLabel({ status: "approved", effective_from: "2026-01-01" }, "2026-11-22"),
    /In force now/,
  );
});

test("labels a version replaced by a later version", () => {
  assert.match(
    versionStateLabel(
      {
        status: "superseded",
        effective_from: "2026-01-01",
        effective_to: "2026-07-01",
        replaced_by_version: 2,
        replaced_by_effective_from: "2026-07-01",
      },
      "2026-11-22",
    ),
    /Replaced by version 2 on 2026-07-01/,
  );
});

test("labels a future version by its start date", () => {
  assert.match(
    versionStateLabel({ status: "approved", effective_from: "2027-01-01" }, "2026-11-22"),
    /Starts on 2027-01-01/,
  );
});
