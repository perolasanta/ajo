"""Grant ajo_scheduler the scoped read/write access needed for cash alerts."""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_scheduler') THEN
            GRANT USAGE ON SCHEMA public TO ajo_scheduler;
            EXECUTE 'CREATE POLICY tenant_directory_scheduler_scan ON tenant_directory '
              'FOR SELECT TO ajo_scheduler USING (true)';
            GRANT SELECT (cooperative_id, status) ON tenant_directory TO ajo_scheduler;
            GRANT SELECT (id, cooperative_id, channel, status, received_on, recorded_by)
              ON payments TO ajo_scheduler;
            GRANT SELECT (cooperative_id, status, effective_from, effective_to, cash_banking_days)
              ON cooperative_policy_versions TO ajo_scheduler;
            GRANT SELECT (id, cooperative_id, status) ON users TO ajo_scheduler;
            GRANT SELECT (cooperative_id, user_id, role) ON user_roles TO ajo_scheduler;
            GRANT SELECT (cooperative_id, user_id) ON telegram_links TO ajo_scheduler;
            GRANT SELECT (cooperative_id, recipient_user_id, kind, entity_id, channel),
                  INSERT (id, cooperative_id, recipient_user_id, entity_id, member_id,
                          kind, channel, template_version, status, payload_encrypted)
              ON notifications TO ajo_scheduler;
            GRANT EXECUTE ON FUNCTION app_current_cooperative_id() TO ajo_scheduler;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_directory_scheduler_scan ON tenant_directory")
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_scheduler') THEN
            REVOKE EXECUTE ON FUNCTION app_current_cooperative_id() FROM ajo_scheduler;
            REVOKE SELECT (cooperative_id, recipient_user_id, kind, entity_id, channel),
                   INSERT (id, cooperative_id, recipient_user_id, entity_id, member_id,
                           kind, channel, template_version, status, payload_encrypted)
              ON notifications FROM ajo_scheduler;
            REVOKE SELECT (cooperative_id, user_id) ON telegram_links FROM ajo_scheduler;
            REVOKE SELECT (cooperative_id, user_id, role) ON user_roles FROM ajo_scheduler;
            REVOKE SELECT (id, cooperative_id, status) ON users FROM ajo_scheduler;
            REVOKE SELECT (cooperative_id, status, effective_from, effective_to, cash_banking_days)
              ON cooperative_policy_versions FROM ajo_scheduler;
            REVOKE SELECT (id, cooperative_id, channel, status, received_on, recorded_by)
              ON payments FROM ajo_scheduler;
            REVOKE SELECT (cooperative_id, status) ON tenant_directory FROM ajo_scheduler;
            REVOKE USAGE ON SCHEMA public FROM ajo_scheduler;
          END IF;
        END $$"""
    )
