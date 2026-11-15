"""Shared SQL fragments for cash banking deadlines."""

CASH_BANKING_POLICY_LATERAL_SQL = """
LEFT JOIN LATERAL (
    SELECT v.cash_banking_days
    FROM cooperative_policy_versions v
    WHERE v.cooperative_id=p.cooperative_id
      AND v.status='approved'
      AND v.effective_from <= p.received_on
      AND (v.effective_to IS NULL OR p.received_on < v.effective_to)
    ORDER BY v.effective_from DESC
    LIMIT 1
) policy ON true
"""

CASH_OVERDUE_PREDICATE_SQL = """(
    p.received_on + COALESCE(policy.cash_banking_days, 2)
    < COALESCE(CAST(:as_of_date AS date), (now() AT TIME ZONE 'Africa/Lagos')::date)
)"""
