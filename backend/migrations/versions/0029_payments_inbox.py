"""Create provider-neutral payment records and cooperative receipt counters.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
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
        CREATE TABLE payment_receipt_counters (
            cooperative_id uuid PRIMARY KEY REFERENCES cooperatives(id),
            last_receipt_no bigint NOT NULL DEFAULT 0 CHECK (last_receipt_no >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE payments (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            member_id uuid NOT NULL,
            channel text NOT NULL CHECK (
                channel IN ('cash','bank_transfer','paystack','virtual_account')
            ),
            provider text NOT NULL DEFAULT 'manual' CHECK (char_length(btrim(provider)) > 0),
            provider_reference text,
            purpose text NOT NULL CHECK (
                purpose IN ('savings','shares','dues','loan_repayment')
            ),
            loan_id uuid,
            amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
            fee_kobo bigint NOT NULL DEFAULT 0 CHECK (fee_kobo >= 0),
            fee_bearer text NOT NULL DEFAULT 'member' CHECK (
                fee_bearer IN ('member','cooperative')
            ),
            status text NOT NULL CHECK (
                status IN ('pending','received','confirmed','rejected','banked')
            ),
            recorded_by uuid NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            received_on date NOT NULL,
            confirmed_by uuid,
            confirmed_at timestamptz,
            rejected_reason text,
            receipt_no bigint,
            ledger_entry_id uuid,
            idempotency_key text NOT NULL CHECK (char_length(btrim(idempotency_key)) > 0),
            request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, idempotency_key),
            UNIQUE (cooperative_id, receipt_no),
            FOREIGN KEY (cooperative_id, member_id) REFERENCES members(cooperative_id, id),
            FOREIGN KEY (cooperative_id, loan_id) REFERENCES loans(cooperative_id, id),
            FOREIGN KEY (cooperative_id, recorded_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, confirmed_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, ledger_entry_id)
                REFERENCES journal_entries(cooperative_id, id),
            CHECK ((channel = 'cash' AND status IN ('received','banked')) OR
                   (channel <> 'cash' AND status IN ('pending','confirmed','rejected'))),
            CHECK ((status = 'rejected' AND rejected_reason IS NOT NULL
                    AND char_length(btrim(rejected_reason)) > 0) OR
                   (status <> 'rejected' AND rejected_reason IS NULL)),
            CHECK (channel <> 'bank_transfer' OR
                   (provider_reference IS NOT NULL AND char_length(btrim(provider_reference)) > 0)),
            CHECK ((purpose = 'loan_repayment' AND loan_id IS NOT NULL) OR
                   (purpose <> 'loan_repayment' AND loan_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX payments_provider_reference_uq "
        "ON payments (cooperative_id, channel, provider_reference) "
        "WHERE provider_reference IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX payments_status_idx ON payments (cooperative_id, status, recorded_at DESC)"
    )
    op.execute(
        "CREATE INDEX payments_member_idx ON payments (cooperative_id, member_id, recorded_at DESC)"
    )

    _tenant_table("payment_receipt_counters")
    _tenant_table("payments")
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON payment_receipt_counters TO ajo_app;
            REVOKE DELETE, TRUNCATE ON payment_receipt_counters FROM ajo_app;
            GRANT SELECT, INSERT, UPDATE ON payments TO ajo_app;
            REVOKE DELETE, TRUNCATE ON payments FROM ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE payments")
    op.execute("DROP TABLE payment_receipt_counters")
