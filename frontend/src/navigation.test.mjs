import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCESS_DENIED_NOTICE,
  accountMenuBlocks,
  DEFAULT_LANDING_PATH,
  disbursementRoles,
  getRouteDecision,
  navigation,
  navigationTarget,
  formatMenuBadge,
  menuButtonLabel,
  totalVisibleActionCount,
  visibleNavigation,
} from "./navigation.js";
import { canDecideLoans, canSeeDestinationAccount, canSubmitLoans } from "./loanPolicy.mjs";
import {
  canAuthorizeDisbursement,
  canConfirmDisbursementPaid,
  canFailDisbursement,
  canPrepareDisbursement,
  canRecordDisbursementTransfer,
  canReverseDisbursement,
  shouldExplainPreparedByCurrentUser,
} from "./disbursementPolicy.mjs";

const cases = [
  ["treasurer", true],
  ["auditor", true],
  ["officer", true],
  ["admin", true],
  ["committee", false],
];

const documentedRoles = ["officer", "committee", "treasurer", "admin", "auditor"];

for (const role of documentedRoles) {
  test(`${role} lands on Dashboard from root and unknown routes`, () => {
    assert.equal(DEFAULT_LANDING_PATH, "/dashboard");
    assert.deepEqual(getRouteDecision([role], "/"), {
      allowed: false,
      redirectTo: "/dashboard",
    });
    assert.deepEqual(getRouteDecision([role], "/not-a-route"), {
      allowed: false,
      redirectTo: "/dashboard",
    });
    assert.equal(getRouteDecision([role], "/dashboard").allowed, true);
  });

  test(`${role} only sees navigation paths it can open`, () => {
    for (const item of visibleNavigation([role])) {
      assert.equal(getRouteDecision([role], item.path).allowed, true, item.path);
    }
  });

  for (const item of navigation.filter((item) => !item.roles.includes(role))) {
    test(`${role} is redirected from forbidden ${item.path} with a notice`, () => {
      assert.deepEqual(getRouteDecision([role], item.path), {
        allowed: false,
        redirectTo: "/dashboard",
        notice: ACCESS_DENIED_NOTICE,
      });
    });
  }
}

test("loan creation is limited to roles that may submit applications", () => {
  assert.equal(getRouteDecision(["officer"], "/loans/new").allowed, true);
  assert.equal(getRouteDecision(["admin"], "/loans/new").allowed, true);
  for (const role of ["committee", "treasurer", "auditor"]) {
    assert.equal(getRouteDecision([role], "/loans/new").notice, ACCESS_DENIED_NOTICE);
  }
});

for (const [role, expected] of cases) {
  test(`${role} ${expected ? "sees" : "does not see"} Members navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Members"), expected);
  });
}

const loanCases = [
  ["admin", true],
  ["officer", true],
  ["treasurer", true],
  ["committee", true],
  ["auditor", true],
  ["member", false],
];

for (const [role, expected] of [
  ["admin", true],
  ["treasurer", true],
  ["auditor", true],
  ["officer", false],
  ["committee", false],
  ["member", false],
]) {
  test(`${role} ${expected ? "sees" : "does not see"} Disbursements navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Disbursements"), expected);
  });
}

test("disbursement navigation allowlist contains only admin, treasurer and auditor", () => {
  assert.deepEqual(disbursementRoles, ["admin", "treasurer", "auditor"]);
});

for (const [role, expected] of [
  ["admin", true],
  ["auditor", true],
  ["officer", false],
  ["treasurer", false],
  ["committee", false],
  ["member", false],
]) {
  test(`${role} ${expected ? "sees" : "does not see"} Audit navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Audit"), expected);
  });
}

for (const [role, expected] of loanCases) {
  test(`${role} ${expected ? "sees" : "does not see"} Loans navigation`, () => {
    const names = visibleNavigation([role]).map((item) => item.name);
    assert.equal(names.includes("Loans"), expected);
  });
}

test("only admin and officer can submit loan applications", () => {
  assert.equal(canSubmitLoans(["admin"]), true);
  assert.equal(canSubmitLoans(["officer"]), true);
  assert.equal(canSubmitLoans(["committee"]), false);
  assert.equal(canSubmitLoans(["treasurer"]), false);
  assert.equal(canSubmitLoans(["auditor"]), false);
});

test("only committee can decide an application under review", () => {
  assert.equal(canDecideLoans(["committee"], "under_review"), true);
  assert.equal(canDecideLoans(["committee"], "approved"), false);
  assert.equal(canDecideLoans(["admin"], "under_review"), false);
});

test("bank details are visible only to bank-authorized roles", () => {
  assert.equal(canSeeDestinationAccount(["admin"]), true);
  assert.equal(canSeeDestinationAccount(["officer"]), true);
  assert.equal(canSeeDestinationAccount(["treasurer"]), true);
  assert.equal(canSeeDestinationAccount(["committee"]), false);
  assert.equal(canSeeDestinationAccount(["auditor"]), false);
});

test("disbursement actions respect role, workflow state and maker-checker separation", () => {
  const pending = {
    status: "pending_authorization",
    prepared_by: "maker",
    authorized_by: null,
    confirmed_by: null,
  };
  const authorized = { ...pending, status: "authorized", authorized_by: "admin-1" };
  const transferRecorded = { ...authorized, status: "transfer_recorded" };
  const paid = { ...transferRecorded, status: "paid", confirmed_by: "admin-1" };
  assert.equal(canPrepareDisbursement(["treasurer"]), true);
  assert.equal(canPrepareDisbursement(["admin"]), false);
  assert.equal(canAuthorizeDisbursement(["admin"], "checker", pending), true);
  assert.equal(canAuthorizeDisbursement(["admin"], "maker", pending), false);
  assert.equal(shouldExplainPreparedByCurrentUser("maker", pending), true);
  assert.equal(canRecordDisbursementTransfer(["treasurer"], "treasurer-1", authorized), true);
  assert.equal(canRecordDisbursementTransfer(["treasurer"], "admin-1", authorized), false);
  assert.equal(canRecordDisbursementTransfer(["admin"], "admin-2", authorized), false);
  assert.equal(canConfirmDisbursementPaid(["admin"], "admin-1", transferRecorded), true);
  assert.equal(canConfirmDisbursementPaid(["admin"], "admin-2", transferRecorded), false);
  assert.equal(canFailDisbursement(["treasurer"], "treasurer-1", authorized), true);
  assert.equal(canFailDisbursement(["treasurer"], "admin-1", authorized), false);
  assert.equal(canReverseDisbursement(["admin"], "admin-2", paid), true);
  assert.equal(canReverseDisbursement(["admin"], "admin-1", paid), false);
});

test("auditor does not see Payroll navigation", () => {
  const names = visibleNavigation(["auditor"]).map((item) => item.name);
  assert.equal(names.includes("Payroll"), false);
});

test("the account footer has one profile block and falls back to email", () => {
  const blocks = accountMenuBlocks({
    full_name: " Aisha Bello ",
    email: "aisha@example.test",
    roles: ["admin", "treasurer"],
  });
  assert.equal(blocks.length, 1);
  assert.deepEqual(blocks[0], {
    path: "/account",
    displayName: "Aisha Bello",
    avatarName: "Aisha Bello",
    email: "aisha@example.test",
    roles: ["admin", "treasurer"],
  });
  assert.equal(
    accountMenuBlocks({ full_name: null, email: "a@example.test", roles: [] })[0].displayName,
    "a@example.test",
  );
});

test("Account is outside role-gated navigation and Notifications redirects there", () => {
  for (const role of ["officer", "committee", "treasurer", "admin", "auditor", "member"]) {
    assert.deepEqual(getRouteDecision([role], "/account"), { allowed: true });
    assert.deepEqual(getRouteDecision([role], "/notifications"), {
      allowed: false,
      redirectTo: "/account",
    });
    assert.equal(
      visibleNavigation([role]).some((item) => ["/account", "/notifications"].includes(item.path)),
      false,
    );
  }
});

test("mobile menu total sums action counts across visible sections", () => {
  const roles = ["admin", "treasurer", "committee"];
  const total = totalVisibleActionCount(
    visibleNavigation(roles),
    {
      payroll: { awaiting_posting: 2, awaiting_sign_off: 3 },
      disbursements: { awaiting_authorization: 4, awaiting_confirmation: 5, ready_to_record: 6 },
      role_changes: { awaiting_approval: 7 },
      loans: { awaiting_decision: 8, disputed_or_paused: 9 },
      withdrawal_requests: { awaiting_approval: 1 },
    },
    roles,
  );
  assert.equal(total, 45);
});

test("withdrawal action badges count only for treasurers", () => {
  const summary = { withdrawal_requests: { awaiting_approval: 3 } };
  assert.equal(totalVisibleActionCount(visibleNavigation(["treasurer"]), summary, ["treasurer"]), 3);
  assert.equal(totalVisibleActionCount(visibleNavigation(["officer"]), summary, ["officer"]), 0);
});

test("mobile menu badge caps totals at 99+", () => {
  const roles = ["treasurer"];
  const total = totalVisibleActionCount(
    visibleNavigation(roles),
    {
      payroll: { awaiting_posting: 100 },
    },
    roles,
  );
  assert.equal(formatMenuBadge(total), "99+");
});

test("mobile menu badge is hidden when total is zero", () => {
  const roles = ["officer"];
  const total = totalVisibleActionCount(visibleNavigation(roles), {}, roles);
  assert.equal(total, 0);
  assert.equal(formatMenuBadge(total), null);
});

test("mobile menu button label announces the current action total", () => {
  assert.equal(menuButtonLabel(0), "Open navigation");
  assert.equal(menuButtonLabel(1), "Menu, 1 item needs action");
  assert.equal(menuButtonLabel(12), "Menu, 12 items need action");
});

test("mobile menu excludes counts for hidden or non-actionable sections", () => {
  const roles = ["officer"];
  const total = totalVisibleActionCount(
    visibleNavigation(roles),
    {
      payroll: { awaiting_posting: 10, awaiting_sign_off: 20 },
      disbursements: { awaiting_authorization: 30, awaiting_confirmation: 40 },
      role_changes: { awaiting_approval: 50 },
      loans: { awaiting_decision: 60, disputed_or_paused: 70 },
    },
    roles,
  );
  assert.equal(total, 0);
});


test("action badges open their filtered queue, not an unfiltered section", () => {
  assert.equal(navigationTarget("Withdrawal requests", 1), "/withdrawal-requests?mine=needed");
  assert.equal(navigationTarget("Payroll", 2), "/payroll?action=needed");
  assert.equal(navigationTarget("Loans", 1), "/loans?action=needed");
  assert.equal(navigationTarget("Members", 0), "/members");
});


test("action deep links remain behind the normal route permission checks", () => {
  assert.deepEqual(getRouteDecision(["treasurer"], "/members/abc?withdrawal_request=req"), { allowed: true });
  assert.deepEqual(getRouteDecision(["committee"], "/withdrawal-requests?request=req"), {
    allowed: false,
    redirectTo: DEFAULT_LANDING_PATH,
    notice: ACCESS_DENIED_NOTICE,
  });
  assert.equal(getRouteDecision(["member"], "/loans/abc").allowed, false);
});
