"""Persist replay results for payment decision actions.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payment_action_idempotency (
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            payment_id uuid NOT NULL,
            action text NOT NULL CHECK (action IN ('confirm','reject')),
            idempotency_key text NOT NULL CHECK (char_length(btrim(idempotency_key)) > 0),
            request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
            result_payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, idempotency_key),
            FOREIGN KEY (cooperative_id, payment_id) REFERENCES payments(cooperative_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX payment_action_idempotency_payment_idx "
        "ON payment_action_idempotency (cooperative_id, payment_id, action)"
    )
    op.execute("ALTER TABLE payment_action_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_action_idempotency FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY payment_action_idempotency_cooperative_isolation
        ON payment_action_idempotency
        USING (cooperative_id = app_current_cooperative_id())
        WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT ON payment_action_idempotency TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON payment_action_idempotency FROM ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE payment_action_idempotency")
