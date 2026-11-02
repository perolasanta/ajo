const loanStatuses = [
  ["under_review", "Loans: under review"],
  ["awaiting_applicant_confirmation", "Loans: awaiting confirmation"],
  ["approved_awaiting_final", "Loans: awaiting final terms"],
  ["ready_for_disbursement", "Loans: ready for disbursement"],
  ["repaying", "Loans: repaying"],
  ["closed", "Loans: closed"],
  ["disputed", "Loans: disputed"],
];

const loanCards = (roles) =>
  roles.some((role) => ["admin", "officer", "treasurer", "auditor"].includes(role))
    ? loanStatuses.map(([status, title]) => ({
        title,
        description: "Loan applications",
        source: "loans",
        status,
        href: `/loans?status=${encodeURIComponent(status)}`,
      }))
    : [];

const disbursementCards = (roles) => {
  if (roles.includes("admin")) {
    return [
      ["pending_authorization", "Awaiting authorization"],
      ["transfer_recorded", "Awaiting confirmation"],
    ].map(([status, title]) => ({
      title,
      description: "Disbursements",
      source: "disbursements",
      status,
      href: `/disbursements?status=${encodeURIComponent(status)}`,
    }));
  }
  if (roles.includes("treasurer")) {
    return [["authorized", "Ready to record transfer"]].map(([status, title]) => ({
      title,
      description: "Disbursements",
      source: "disbursements",
      status,
      href: `/disbursements?status=${encodeURIComponent(status)}`,
    }));
  }
  if (roles.includes("auditor")) {
    return [
      ["pending_authorization", "Disbursements: awaiting authorization"],
      ["authorized", "Disbursements: authorized"],
      ["transfer_recorded", "Disbursements: awaiting confirmation"],
      ["paid", "Disbursements: paid"],
      ["failed", "Disbursements: failed"],
      ["cancelled", "Disbursements: cancelled"],
    ].map(([status, title]) => ({
      title,
      description: "Disbursements",
      source: "disbursements",
      status,
      href: `/disbursements?status=${encodeURIComponent(status)}`,
    }));
  }
  return [];
};

export function dashboardCards(roles = []) {
  const cards = [...loanCards(roles), ...disbursementCards(roles)];
  if (roles.includes("committee")) {
    cards.push(
      {
        title: "Applications under review",
        description: "Loan applications",
        source: "loans",
        status: "under_review",
        href: "/loans?status=under_review",
      },
    );
  }
  return cards;
}
