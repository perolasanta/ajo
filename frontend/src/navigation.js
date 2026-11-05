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

  const route = path === "/loans/new" ? null : routePatterns.find(([, pattern]) => pattern.test(path));
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
