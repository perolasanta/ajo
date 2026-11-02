export type DashboardCard = {
  title: string;
  description: string;
  source: "loans" | "disbursements";
  status: string;
  href: string;
};

export function dashboardCards(roles?: string[]): DashboardCard[];
