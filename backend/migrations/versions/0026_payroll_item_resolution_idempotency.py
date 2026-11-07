"""Track payroll exception-note timestamps and idempotent writes.

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE payroll_items ADD COLUMN resolved_at timestamptz")
    op.execute(
        """CREATE TABLE payroll_item_resolution_idempotency (
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
          request_hash bytea NOT NULL CHECK (octet_length(request_hash)=32),
          item_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,idempotency_key),
          UNIQUE (cooperative_id,item_id),
          FOREIGN KEY (cooperative_id,item_id)
            REFERENCES payroll_items(cooperative_id,id)
        )"""
    )
    op.execute("ALTER TABLE payroll_item_resolution_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_item_resolution_idempotency FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY payroll_item_resolution_idempotency_tenant
          ON payroll_item_resolution_idempotency
          USING (cooperative_id=app_current_cooperative_id())
          WITH CHECK (cooperative_id=app_current_cooperative_id())"""
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
          GRANT SELECT,INSERT ON payroll_item_resolution_idempotency TO ajo_app;
          REVOKE UPDATE,DELETE,TRUNCATE ON payroll_item_resolution_idempotency FROM ajo_app;
          GRANT UPDATE (resolution_note,resolved_by,resolved_at) ON payroll_items TO ajo_app;
        END IF; END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE payroll_item_resolution_idempotency")
    op.execute("ALTER TABLE payroll_items DROP COLUMN resolved_at")
