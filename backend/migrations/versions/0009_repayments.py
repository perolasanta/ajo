"""Repayment receipts, allocation waterfall, and arrears read model.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} USING "
        "(cooperative_id=app_current_cooperative_id()) WITH CHECK "
        "(cooperative_id=app_current_cooperative_id())"
    )


def upgrade() -> None:
    op.execute("""ALTER TABLE cooperative_policy_versions ADD COLUMN payroll_allocation_order text[]
      NOT NULL DEFAULT ARRAY['dues','interest','principal','savings']::text[]
      CHECK (array_length(payroll_allocation_order, 1) = 4
        AND payroll_allocation_order @> ARRAY['dues','interest','principal','savings']::text[])""")
    op.execute("ALTER TABLE loan_schedule ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute(
        "ALTER TABLE loan_schedule ADD COLUMN dues_kobo bigint NOT NULL DEFAULT 0 CHECK (dues_kobo >= 0)"
    )
    op.execute(
        "ALTER TABLE loan_schedule ADD COLUMN savings_kobo bigint NOT NULL DEFAULT 0 CHECK (savings_kobo >= 0)"
    )
    op.execute(
        "ALTER TABLE loan_schedule ADD CONSTRAINT loan_schedule_id_unique UNIQUE (cooperative_id,id)"
    )
    op.execute("""CREATE TABLE repayments (
      id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      loan_id uuid NOT NULL, member_id uuid NOT NULL, amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
      source text NOT NULL CHECK (source IN ('payroll','bank_transfer','cash')), received_on date NOT NULL,
      reference text NOT NULL, status text NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','reversed')),
      journal_entry_id uuid NOT NULL, idempotency_key text NOT NULL,
      reversed_by uuid, reversed_at timestamptz, reversal_reason text, reversal_entry_id uuid,
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (cooperative_id,id), UNIQUE (cooperative_id,idempotency_key),
      FOREIGN KEY (cooperative_id,loan_id) REFERENCES loans(cooperative_id,id),
      FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
      FOREIGN KEY (cooperative_id,journal_entry_id) REFERENCES journal_entries(cooperative_id,id),
      FOREIGN KEY (cooperative_id,reversed_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,reversal_entry_id) REFERENCES journal_entries(cooperative_id,id),
      FOREIGN KEY (cooperative_id,created_by) REFERENCES users(cooperative_id,id),
      CHECK ((status = 'posted' AND reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL AND reversal_entry_id IS NULL)
        OR (status = 'reversed' AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL
          AND reversal_reason IS NOT NULL AND reversal_entry_id IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE repayment_allocations (
      id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      repayment_id uuid NOT NULL, schedule_id uuid NOT NULL, component text NOT NULL
        CHECK (component IN ('interest','principal','dues','savings')),
      amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
      status text NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','reversed')),
      PRIMARY KEY (cooperative_id,id),
      FOREIGN KEY (cooperative_id,repayment_id) REFERENCES repayments(cooperative_id,id),
      FOREIGN KEY (cooperative_id,schedule_id) REFERENCES loan_schedule(cooperative_id,id),
      UNIQUE (cooperative_id,repayment_id,schedule_id,component)
    )""")
    for table in ("repayments", "repayment_allocations"):
        _rls(table)
    op.execute(
        "CREATE INDEX repayment_allocations_schedule_idx ON repayment_allocations(cooperative_id,schedule_id) WHERE status='posted'"
    )
    op.execute("ALTER TABLE fraud_alerts DROP CONSTRAINT fraud_alerts_rule_code_check")
    op.execute("""ALTER TABLE fraud_alerts ADD CONSTRAINT fraud_alerts_rule_code_check CHECK (rule_code IN
      ('parallel_loan','third_party_beneficiary','destination_changed','same_user_authorization','consent_dispute','paused_deduction_receipt','unallocated_receipt'))""")
    op.execute("""CREATE OR REPLACE FUNCTION arrears(p_cooperative_id uuid, p_as_of date)
      RETURNS TABLE (loan_id uuid, member_id uuid, amount_kobo bigint)
      LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
        SELECT s.loan_id, l.member_id,
          sum(s.principal_kobo + s.interest_kobo + s.dues_kobo + s.savings_kobo
            - coalesce(a.principal_kobo,0) - coalesce(a.interest_kobo,0)
            - coalesce(a.dues_kobo,0) - coalesce(a.savings_kobo,0))::bigint
        FROM loan_schedule s JOIN loans l ON l.cooperative_id=s.cooperative_id AND l.id=s.loan_id
        LEFT JOIN LATERAL (SELECT
          sum(amount_kobo) FILTER (WHERE component='principal') principal_kobo,
          sum(amount_kobo) FILTER (WHERE component='interest') interest_kobo,
          sum(amount_kobo) FILTER (WHERE component='dues') dues_kobo,
          sum(amount_kobo) FILTER (WHERE component='savings') savings_kobo
          FROM repayment_allocations ra WHERE ra.cooperative_id=s.cooperative_id
            AND ra.schedule_id=s.id AND ra.status='posted') a ON true
        WHERE s.cooperative_id=p_cooperative_id AND app_current_cooperative_id()=p_cooperative_id
          AND (s.due_at AT TIME ZONE 'Africa/Lagos')::date <= p_as_of
        GROUP BY s.loan_id,l.member_id HAVING sum(s.principal_kobo+s.interest_kobo+s.dues_kobo+s.savings_kobo
          -coalesce(a.principal_kobo,0)-coalesce(a.interest_kobo,0)-coalesce(a.dues_kobo,0)-coalesce(a.savings_kobo,0)) > 0
      $$""")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT SELECT,INSERT,UPDATE ON repayments,repayment_allocations TO ajo_app;
      REVOKE DELETE,TRUNCATE ON repayments,repayment_allocations FROM ajo_app;
      GRANT EXECUTE ON FUNCTION arrears(uuid,date) TO ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute("DROP FUNCTION arrears(uuid,date)")
    op.execute("DROP TABLE repayment_allocations")
    op.execute("DROP TABLE repayments")
    op.execute("ALTER TABLE loan_schedule DROP CONSTRAINT loan_schedule_id_unique")
    op.execute(
        "ALTER TABLE loan_schedule DROP COLUMN savings_kobo, DROP COLUMN dues_kobo, DROP COLUMN id"
    )
    op.execute("ALTER TABLE cooperative_policy_versions DROP COLUMN payroll_allocation_order")
    op.execute("ALTER TABLE fraud_alerts DROP CONSTRAINT fraud_alerts_rule_code_check")
    op.execute("""ALTER TABLE fraud_alerts ADD CONSTRAINT fraud_alerts_rule_code_check CHECK (rule_code IN
      ('parallel_loan','third_party_beneficiary','destination_changed','same_user_authorization','consent_dispute'))""")
