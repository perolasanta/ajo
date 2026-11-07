export const DEFAULT_LANDING_PATH = "/dashboard";
export const ACCESS_DENIED_NOTICE = "You do not have access to that page";
export const disbursementRoles = ["admin", "treasurer", "auditor"];

export const navigation = [
  {
    name: "Dashboard",
    path: "/dashboard",
    roles: ["officer", "committee", "treasurer", "admin", "auditor"],
  },
  { name: "Members", path: "/members", roles: ["officer", "treasurer", "auditor", "admin"] },
  {
    name: "Loans",
    path: "/loans",
    roles: ["officer", "committee", "treasurer", "admin", "auditor"],
  },
  { name: "Disbursements", path: "/disbursements", roles: disbursementRoles },
  {
    name: "Payroll",
    path: "/payroll",
    roles: ["treasurer", "officer", "admin"],
  },
  { name: "Audit", path: "/audit", roles: ["admin", "auditor"] },
  { name: "Staff", path: "/staff", roles: ["admin"] },
];

export function visibleNavigation(roles) {
  return navigation.filter((item) => item.roles.some((role) => roles.includes(role)));
}

export function menuActionCount(name, summary, roles) {
  let count = 0;
  if (name === "Payroll") {
    if (roles.includes("treasurer")) count += summary?.payroll?.awaiting_posting ?? 0;
    if (roles.includes("admin")) count += summary?.payroll?.awaiting_sign_off ?? 0;
  }
  if (name === "Disbursements") {
    if (roles.includes("admin")) {
      count +=
        (summary?.disbursements?.awaiting_authorization ?? 0) +
        (summary?.disbursements?.awaiting_confirmation ?? 0);
    }
    if (roles.includes("treasurer")) {
      count += summary?.disbursements?.ready_to_record ?? 0;
    }
  }
  if (name === "Staff" && roles.includes("admin")) {
    count += summary?.role_changes?.awaiting_approval ?? 0;
  }
  if (name === "Loans") {
    if (roles.includes("committee")) count += summary?.loans?.awaiting_decision ?? 0;
    if (roles.includes("treasurer")) count += summary?.loans?.disputed_or_paused ?? 0;
  }
  return count;
}

export function totalVisibleActionCount(visibleLinks, summary, roles) {
  return visibleLinks.reduce(
    (total, item) => total + menuActionCount(item.name, summary, roles),
    0,
  );
}

export function formatMenuBadge(count) {
  if (count <= 0) return null;
  return count > 99 ? "99+" : String(count);
}

export function menuButtonLabel(count) {
  if (count <= 0) return "Open navigation";
  return `Menu, ${count} ${count === 1 ? "item needs" : "items need"} action`;
}

const routePatterns = [
  ["/dashboard", /^\/dashboard$/],
  ["/members", /^\/members(?:\/[^/]+)?$/],
  ["/loans", /^\/loans(?:\/[^/]+)?$/],
  ["/disbursements", /^\/disbursements(?:\/[^/]+)?$/],
  ["/payroll", /^\/payroll(?:\/runs\/[^/]+)?$/],
  ["/audit", /^\/audit$/],
  ["/staff", /^\/staff$/],
];

export function getRouteDecision(roles, pathname) {
  const path = pathname.split(/[?#]/, 1)[0].replace(/\/+$/, "") || "/";
  if (path === "/") return { allowed: false, redirectTo: DEFAULT_LANDING_PATH };
  if (path === "/notifications") {
    return {
      allowed: ["officer", "committee", "treasurer", "admin", "auditor"].some((role) =>
        roles.includes(role),
      ),
      redirectTo: DEFAULT_LANDING_PATH,
    };
  }

  const route =
    path === "/loans/new" ? null : routePatterns.find(([, pattern]) => pattern.test(path));
  const item = route && navigation.find((entry) => entry.path === route[0]);
  const allowedRoles = path === "/loans/new" ? ["admin", "officer"] : item?.roles;
  if (!allowedRoles) return { allowed: false, redirectTo: DEFAULT_LANDING_PATH };
  if (allowedRoles.some((role) => roles.includes(role))) return { allowed: true };
  return {
    allowed: false,
    redirectTo: DEFAULT_LANDING_PATH,
    notice: ACCESS_DENIED_NOTICE,
  };
}
