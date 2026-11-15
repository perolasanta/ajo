"""Add share purchase requests and per-cooperative share pricing.

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_cooperative_isolation ON {table}
            USING (cooperative_id = app_current_cooperative_id())
            WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )


def upgrade() -> None:
    op.execute(
        """ALTER TABLE cooperatives
        ADD COLUMN share_unit_price_naira bigint NOT NULL DEFAULT 1
          CHECK (share_unit_price_naira > 0)"""
    )
    op.execute(
        """CREATE TABLE share_purchase_requests (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL,
          shares_count bigint NOT NULL CHECK (shares_count > 0),
          unit_price_naira bigint NOT NULL CHECK (unit_price_naira > 0),
          amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
          status text NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected','cancelled')),
          requested_by uuid NOT NULL,
          decided_by uuid,
          decision_note text,
          journal_entry_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          decided_at timestamptz,
          cancelled_at timestamptz,
          PRIMARY KEY (cooperative_id, id),
          FOREIGN KEY (cooperative_id, member_id)
            REFERENCES members(cooperative_id, id),
          FOREIGN KEY (cooperative_id, requested_by)
            REFERENCES users(cooperative_id, id),
          FOREIGN KEY (cooperative_id, decided_by)
            REFERENCES users(cooperative_id, id),
          FOREIGN KEY (cooperative_id, journal_entry_id)
            REFERENCES journal_entries(cooperative_id, id),
          CHECK (amount_kobo = shares_count * unit_price_naira * 100),
          CHECK ((status = 'rejected' AND decision_note IS NOT NULL
                  AND char_length(btrim(decision_note)) > 0)
                 OR status <> 'rejected'),
          CHECK ((status IN ('approved','rejected') AND decided_by IS NOT NULL
                  AND decided_at IS NOT NULL)
                 OR status IN ('pending','cancelled')),
          CHECK ((status = 'approved' AND journal_entry_id IS NOT NULL)
                 OR status <> 'approved'),
          CHECK ((status = 'cancelled' AND cancelled_at IS NOT NULL)
                 OR status <> 'cancelled')
        )"""
    )
    op.execute(
        """CREATE UNIQUE INDEX share_purchase_pending_member_uq
        ON share_purchase_requests (cooperative_id, member_id)
        WHERE status = 'pending'"""
    )
    op.execute(
        """CREATE INDEX share_purchase_member_created_idx
        ON share_purchase_requests (cooperative_id, member_id, created_at DESC)"""
    )
    _tenant_rls("share_purchase_requests")
    op.execute(
        """INSERT INTO accounts (cooperative_id, code, name, account_type)
        SELECT id, 'member_shares', 'Compulsory member shares', 'equity'
        FROM cooperatives
        ON CONFLICT (cooperative_id, code) DO NOTHING"""
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON share_purchase_requests TO ajo_app;
            REVOKE DELETE, TRUNCATE ON share_purchase_requests FROM ajo_app;
            GRANT SELECT, UPDATE ON cooperatives TO ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE share_purchase_requests")
    op.execute("ALTER TABLE cooperatives DROP COLUMN share_unit_price_naira")
