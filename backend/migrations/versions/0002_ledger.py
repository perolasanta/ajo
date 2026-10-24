"""double-entry ledger with tenant isolation and append-only enforcement

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE accounts (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            code text NOT NULL,
            name text NOT NULL,
            account_type text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE journal_entries (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            posted_at timestamptz NOT NULL DEFAULT now(),
            effective_date date NOT NULL,
            source_type text NOT NULL,
            source_id uuid,
            description text NOT NULL,
            created_by uuid,
            reverses_entry_id uuid,
            idempotency_key text NOT NULL,
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, idempotency_key),
            UNIQUE (cooperative_id, reverses_entry_id),
            CHECK (reverses_entry_id IS NULL OR reverses_entry_id <> id),
            FOREIGN KEY (cooperative_id, reverses_entry_id)
                REFERENCES journal_entries (cooperative_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE journal_lines (
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            entry_id uuid NOT NULL,
            line_number integer NOT NULL,
            account_id uuid NOT NULL,
            member_id uuid,
            loan_id uuid,
            debit_kobo bigint NOT NULL DEFAULT 0,
            credit_kobo bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (cooperative_id, entry_id, line_number),
            FOREIGN KEY (cooperative_id, entry_id)
                REFERENCES journal_entries (cooperative_id, id),
            FOREIGN KEY (cooperative_id, account_id)
                REFERENCES accounts (cooperative_id, id),
            CHECK (
                (debit_kobo > 0 AND credit_kobo = 0)
                OR (credit_kobo > 0 AND debit_kobo = 0)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX journal_entries_cooperative_posted_idx "
        "ON journal_entries (cooperative_id, posted_at DESC)"
    )
    op.execute(
        "CREATE INDEX journal_lines_account_idx "
        "ON journal_lines (cooperative_id, account_id, entry_id)"
    )

    for table in ("accounts", "journal_entries", "journal_lines"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_cooperative_isolation ON {table}
                USING (cooperative_id = app_current_cooperative_id())
                WITH CHECK (cooperative_id = app_current_cooperative_id())
            """
        )

    op.execute(
        """
        CREATE FUNCTION reject_ledger_mutation() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = '55000';
        END
        $$
        """
    )
    for table in ("journal_entries", "journal_lines"):
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_ledger_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate "
            f"BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_ledger_mutation()"
        )

    op.execute(
        """
        CREATE FUNCTION enforce_journal_entry_balance() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            entry_cooperative_id uuid;
            entry_id_value uuid;
            line_count bigint;
            debit_total numeric;
            credit_total numeric;
        BEGIN
            IF TG_TABLE_NAME = 'journal_entries' THEN
                entry_cooperative_id := NEW.cooperative_id;
                entry_id_value := NEW.id;
            ELSE
                entry_cooperative_id := NEW.cooperative_id;
                entry_id_value := NEW.entry_id;
            END IF;

            SELECT count(*), coalesce(sum(debit_kobo), 0), coalesce(sum(credit_kobo), 0)
              INTO line_count, debit_total, credit_total
              FROM journal_lines
             WHERE cooperative_id = entry_cooperative_id
               AND entry_id = entry_id_value;

            IF line_count < 2 THEN
                RAISE EXCEPTION 'journal entry % must have at least two lines', entry_id_value
                    USING ERRCODE = '23514';
            END IF;
            IF debit_total <> credit_total THEN
                RAISE EXCEPTION 'journal entry % is unbalanced: debits %, credits %',
                    entry_id_value, debit_total, credit_total
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER journal_entries_balance_at_commit
        AFTER INSERT ON journal_entries
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_journal_entry_balance()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER journal_lines_balance_at_commit
        AFTER INSERT ON journal_lines
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_journal_entry_balance()
        """
    )

    op.execute(
        """
        CREATE FUNCTION post_journal_entry(
            p_cooperative_id uuid,
            p_effective_date date,
            p_source_type text,
            p_source_id uuid,
            p_description text,
            p_created_by uuid,
            p_idempotency_key text,
            p_lines jsonb,
            p_reverses_entry_id uuid DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            entry_id_value uuid;
        BEGIN
            IF app_current_cooperative_id() IS DISTINCT FROM p_cooperative_id THEN
                RAISE EXCEPTION 'cooperative context does not match journal entry tenant'
                    USING ERRCODE = '42501';
            END IF;
            IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'journal lines must be a JSON array'
                    USING ERRCODE = '22023';
            END IF;

            INSERT INTO journal_entries (
                cooperative_id, effective_date, source_type, source_id,
                description, created_by, idempotency_key, reverses_entry_id
            ) VALUES (
                p_cooperative_id, p_effective_date, p_source_type, p_source_id,
                p_description, p_created_by, p_idempotency_key, p_reverses_entry_id
            )
            ON CONFLICT (cooperative_id, idempotency_key) DO NOTHING
            RETURNING id INTO entry_id_value;

            IF entry_id_value IS NULL THEN
                SELECT id INTO entry_id_value
                  FROM journal_entries
                 WHERE cooperative_id = p_cooperative_id
                   AND idempotency_key = p_idempotency_key;
                RETURN entry_id_value;
            END IF;

            INSERT INTO journal_lines (
                cooperative_id, entry_id, line_number, account_id,
                member_id, loan_id, debit_kobo, credit_kobo
            )
            SELECT p_cooperative_id,
                   entry_id_value,
                   line.ordinality::integer,
                   (line.value ->> 'account_id')::uuid,
                   nullif(line.value ->> 'member_id', '')::uuid,
                   nullif(line.value ->> 'loan_id', '')::uuid,
                   coalesce((line.value ->> 'debit_kobo')::bigint, 0),
                   coalesce((line.value ->> 'credit_kobo')::bigint, 0)
              FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS line(value, ordinality);

            RETURN entry_id_value;
        END
        $$
        """
    )

    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT EXECUTE ON FUNCTION app_current_cooperative_id() TO ajo_app;
            GRANT SELECT, INSERT ON accounts, journal_entries, journal_lines TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON journal_entries, journal_lines FROM ajo_app;
            GRANT EXECUTE ON FUNCTION post_journal_entry(
                uuid, date, text, uuid, text, uuid, text, jsonb, uuid
            ) TO ajo_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION post_journal_entry(uuid, date, text, uuid, text, uuid, text, jsonb, uuid)"
    )
    op.execute("DROP TRIGGER journal_lines_balance_at_commit ON journal_lines")
    op.execute("DROP TRIGGER journal_entries_balance_at_commit ON journal_entries")
    op.execute("DROP FUNCTION enforce_journal_entry_balance()")
    for table in ("journal_entries", "journal_lines"):
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    op.execute("DROP FUNCTION reject_ledger_mutation()")
    for table in ("journal_lines", "journal_entries", "accounts"):
        op.execute(f"DROP TABLE {table}")
