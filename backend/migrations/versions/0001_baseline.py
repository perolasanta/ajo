"""baseline: cooperatives (tenants) and the RLS tenant-context helper

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reads the tenant set by app.core.db.transaction() via set_config(..., true).
    op.execute(
        """
        CREATE FUNCTION app_current_cooperative_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.cooperative_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE TABLE cooperatives (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text NOT NULL,
            slug        text NOT NULL UNIQUE,
            status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE cooperatives ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cooperatives FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY cooperatives_isolation ON cooperatives
            USING (id = app_current_cooperative_id())
            WITH CHECK (id = app_current_cooperative_id())
        """
    )
    # Least privilege: app may read, create and update cooperatives, never delete.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON cooperatives TO ajo_app;
            GRANT EXECUTE ON FUNCTION app_current_cooperative_id() TO ajo_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE cooperatives")
    op.execute("DROP FUNCTION app_current_cooperative_id()")
