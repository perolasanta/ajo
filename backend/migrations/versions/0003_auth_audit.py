"""tenant directory, staff authentication and append-only audit chain

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_cooperative_isolation ON {table}
            USING (cooperative_id = app_current_cooperative_id())
            WITH CHECK (cooperative_id = app_current_cooperative_id())
        """
    )


def upgrade() -> None:
    # This directory is the deliberately small, non-sensitive pre-tenant lookup.
    op.execute(
        """
        CREATE TABLE tenant_directory (
            slug text PRIMARY KEY,
            cooperative_id uuid NOT NULL UNIQUE REFERENCES cooperatives(id),
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'suspended', 'closed')),
            CHECK (slug = lower(slug) AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            email text NOT NULL,
            password_hash text NOT NULL,
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled')),
            mfa_secret_encrypted text,
            mfa_enabled_at timestamptz,
            failed_login_count integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
            locked_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, id),
            CHECK (email = lower(btrim(email)))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX users_email_per_cooperative_ci ON users (cooperative_id, lower(email))"
    )

    op.execute(
        """
        CREATE TABLE user_roles (
            cooperative_id uuid NOT NULL,
            user_id uuid NOT NULL,
            role text NOT NULL CHECK (role IN ('officer', 'committee', 'treasurer', 'admin', 'auditor')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, user_id, role),
            FOREIGN KEY (cooperative_id, user_id) REFERENCES users (cooperative_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE user_sessions (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL,
            user_id uuid NOT NULL,
            token_hash bytea NOT NULL CHECK (octet_length(token_hash) = 32),
            csrf_token_hash bytea NOT NULL CHECK (octet_length(csrf_token_hash) = 32),
            expires_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            ip inet,
            user_agent text,
            revoked_at timestamptz,
            mfa_pending boolean NOT NULL DEFAULT false,
            authenticated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (token_hash),
            FOREIGN KEY (cooperative_id, user_id) REFERENCES users (cooperative_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX user_sessions_user_active_idx "
        "ON user_sessions (cooperative_id, user_id, expires_at) WHERE revoked_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE recovery_codes (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL,
            user_id uuid NOT NULL,
            code_hash bytea NOT NULL CHECK (octet_length(code_hash) = 32),
            created_at timestamptz NOT NULL DEFAULT now(),
            used_at timestamptz,
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, user_id, code_hash),
            FOREIGN KEY (cooperative_id, user_id) REFERENCES users (cooperative_id, id)
        )
        """
    )

    # The key is an HMAC of request identifiers; no email, slug or IP is stored here.
    # This table also rate-limits attempts for unknown slugs before tenant context exists.
    op.execute(
        """
        CREATE TABLE auth_rate_limits (
            request_key_hash bytea PRIMARY KEY CHECK (octet_length(request_key_hash) = 32),
            failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            window_started_at timestamptz NOT NULL DEFAULT now(),
            locked_until timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE audit_logs (
            id uuid NOT NULL,
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            created_at timestamptz NOT NULL,
            actor_user_id uuid,
            role text,
            action text NOT NULL,
            entity_type text NOT NULL,
            entity_id text,
            summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            channel text NOT NULL,
            ip inet,
            reason text,
            prev_hash bytea NOT NULL CHECK (octet_length(prev_hash) = 32),
            hash bytea NOT NULL CHECK (octet_length(hash) = 32),
            PRIMARY KEY (cooperative_id, id),
            FOREIGN KEY (cooperative_id, actor_user_id) REFERENCES users (cooperative_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX audit_logs_chain_order_idx ON audit_logs (cooperative_id, created_at, id)"
    )

    for table in ("users", "user_roles", "user_sessions", "recovery_codes", "audit_logs"):
        _tenant_rls(table)

    op.execute(
        """
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only; % is not allowed', TG_OP
                USING ERRCODE = '55000';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
        """
    )

    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT EXECUTE ON FUNCTION app_current_cooperative_id() TO ajo_app;
            GRANT SELECT, INSERT ON tenant_directory TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON tenant_directory FROM ajo_app;
            GRANT SELECT, INSERT, UPDATE ON users TO ajo_app;
            GRANT SELECT, INSERT, DELETE ON user_roles TO ajo_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO ajo_app;
            GRANT SELECT, INSERT, UPDATE ON recovery_codes TO ajo_app;
            GRANT SELECT, INSERT, UPDATE ON auth_rate_limits TO ajo_app;
            GRANT SELECT, INSERT ON audit_logs TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM ajo_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    for table in ("audit_logs", "recovery_codes", "user_sessions", "user_roles", "users"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP TABLE auth_rate_limits")
    op.execute("DROP TABLE tenant_directory")
    op.execute("DROP FUNCTION reject_audit_mutation()")
