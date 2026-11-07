"""Add tenant-isolated Telegram account linking tables.
Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_cooperative_isolation ON {table} USING (cooperative_id = app_current_cooperative_id()) WITH CHECK (cooperative_id = app_current_cooperative_id())")

def upgrade() -> None:
    op.execute("""CREATE TABLE telegram_link_tokens (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
        user_id uuid NOT NULL,
        token_hash bytea NOT NULL CHECK (octet_length(token_hash)=32),
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (cooperative_id, token_hash),
        FOREIGN KEY (cooperative_id,user_id) REFERENCES users(cooperative_id,id)
    )""")
    op.execute("CREATE INDEX telegram_link_tokens_user_created_idx ON telegram_link_tokens(cooperative_id,user_id,created_at)")
    op.execute("""CREATE TABLE telegram_links (
        cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
        user_id uuid NOT NULL,
        chat_id text NOT NULL UNIQUE,
        linked_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (cooperative_id,user_id),
        FOREIGN KEY (cooperative_id,user_id) REFERENCES users(cooperative_id,id)
    )""")
    _rls("telegram_link_tokens")
    _rls("telegram_links")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
        GRANT SELECT, INSERT, UPDATE ON telegram_link_tokens TO ajo_app;
        GRANT SELECT, INSERT, DELETE ON telegram_links TO ajo_app;
    END IF; END $$""")

def downgrade() -> None:
    op.execute("DROP TABLE telegram_links")
    op.execute("DROP TABLE telegram_link_tokens")
