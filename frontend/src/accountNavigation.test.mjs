import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { getRouteDecision } from "./navigation.js";

const roles = ["officer", "committee", "treasurer", "admin", "auditor", "member"];

test("every role can open account settings outside role-gated sections", () => {
  for (const role of roles) {
    assert.deepEqual(getRouteDecision([role], "/account"), { allowed: true }, role);
  }
});

test("the old notifications route redirects every role to account settings", () => {
  for (const role of roles) {
    assert.deepEqual(
      getRouteDecision([role], "/notifications"),
      { allowed: false, redirectTo: "/account" },
      role,
    );
  }
  const source = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  assert.ok(
    source.includes('<Route path="/notifications" element={<Navigate to="/account" replace />} />'),
  );
});

test("desktop and mobile navigation share one linked identity block and no avatar menu", () => {
  const source = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  assert.equal((source.match(/<AccountIdentityLink\b/g) ?? []).length, 1);
  assert.equal((source.match(/<Avatar\b/g) ?? []).length, 1);
  assert.equal((source.match(/\{navigationContent\}/g) ?? []).length, 2);
  assert.match(source, /function AccountIdentityLink[\s\S]*?to=\{account\.path\}/);
  assert.doesNotMatch(source, /UserMenu|to="\/notifications"/);
});
