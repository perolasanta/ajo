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
    roles: ["officer", "committee", "admin", "auditor"],
  },
  { name: "Disbursements", path: "/disbursements", roles: ["treasurer", "admin", "auditor"] },
  {
    name: "Payroll",
    path: "/payroll",
    roles: ["treasurer", "officer", "admin", "auditor"],
  },
  { name: "Audit", path: "/audit", roles: ["admin", "auditor"] },
];

export function visibleNavigation(roles) {
  return navigation.filter((item) => item.roles.some((role) => roles.includes(role)));
}
