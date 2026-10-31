import assert from "node:assert/strict";
import test from "node:test";
import { visibleNavigation } from "./navigation.js";

const cases = [
  ["treasurer", true],
  ["auditor", true],
  ["officer", true],
  ["admin", true],
  ["committee", false],
];

for (const [role, expected] of cases) {
  test(`${role} ${expected ? "sees" : "does not see"} Members navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Members"), expected);
  });
}
