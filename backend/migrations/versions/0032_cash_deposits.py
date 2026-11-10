"""Persist confirmed bank deposits for cash receipts.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _tenant_table(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_cooperative_isolation ON {table}
        USING (cooperative_id = app_current_cooperative_id())
        WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE cash_deposits (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            slip_reference text NOT NULL CHECK (char_length(btrim(slip_reference)) BETWEEN 1 AND 160),
            deposited_on date NOT NULL,
            amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
            confirmed_by uuid NOT NULL,
            confirmed_at timestamptz NOT NULL DEFAULT now(),
            ledger_entry_id uuid NOT NULL,
            idempotency_key text NOT NULL CHECK (char_length(btrim(idempotency_key)) > 0),
            request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, slip_reference),
            UNIQUE (cooperative_id, idempotency_key),
            FOREIGN KEY (cooperative_id, confirmed_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, ledger_entry_id)
                REFERENCES journal_entries(cooperative_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE cash_deposit_receipts (
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            deposit_id uuid NOT NULL,
            payment_id uuid NOT NULL,
            PRIMARY KEY (cooperative_id, deposit_id, payment_id),
            UNIQUE (cooperative_id, payment_id),
            FOREIGN KEY (cooperative_id, deposit_id)
                REFERENCES cash_deposits(cooperative_id, id),
            FOREIGN KEY (cooperative_id, payment_id)
                REFERENCES payments(cooperative_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX cash_deposits_recent_idx ON cash_deposits (cooperative_id, deposited_on DESC)"
    )
    for table in ("cash_deposits", "cash_deposit_receipts"):
        _tenant_table(table)
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT ON cash_deposits, cash_deposit_receipts TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON cash_deposits, cash_deposit_receipts FROM ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE cash_deposit_receipts")
    op.execute("DROP TABLE cash_deposits")
