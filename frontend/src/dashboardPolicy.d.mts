export type DashboardCard = {
  title: string;
  description: string;
  source: "loans" | "disbursements" | "action";
  status: string;
  href: string;
  actionType?: string;
};

export function dashboardCards(roles?: string[]): DashboardCard[];
