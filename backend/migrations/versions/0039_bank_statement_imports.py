"""Bank statement evidence imports and parsed lines.
Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
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
        """
        CREATE TABLE bank_statement_imports (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            file_hash text NOT NULL CHECK (file_hash ~ '^[0-9a-f]{64}$'),
            filename text NOT NULL CHECK (char_length(btrim(filename)) BETWEEN 1 AND 255),
            uploaded_by uuid NOT NULL,
            row_count integer NOT NULL CHECK (row_count >= 0),
            inserted_count integer NOT NULL CHECK (inserted_count >= 0),
            skipped_count integer NOT NULL CHECK (skipped_count >= 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, id),
            UNIQUE (cooperative_id, file_hash),
            FOREIGN KEY (cooperative_id, uploaded_by) REFERENCES users(cooperative_id, id),
            CHECK (inserted_count + skipped_count <= row_count)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE bank_statement_lines (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL,
            import_id uuid NOT NULL,
            line_date date NOT NULL,
            narration text NOT NULL CHECK (char_length(btrim(narration)) BETWEEN 1 AND 2000),
            reference text CHECK (reference IS NULL OR char_length(btrim(reference)) BETWEEN 1 AND 160),
            credit_kobo bigint NOT NULL DEFAULT 0 CHECK (credit_kobo >= 0),
            debit_kobo bigint NOT NULL DEFAULT 0 CHECK (debit_kobo >= 0),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            status text NOT NULL DEFAULT 'unmatched'
                CHECK (status IN ('unmatched','suggested','matched','ignored')),
            matched_payment_id uuid,
            ignored_note text,
            decided_by uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (cooperative_id, id),
            FOREIGN KEY (cooperative_id, import_id)
                REFERENCES bank_statement_imports(cooperative_id, id),
            FOREIGN KEY (cooperative_id, matched_payment_id)
                REFERENCES payments(cooperative_id, id),
            FOREIGN KEY (cooperative_id, decided_by) REFERENCES users(cooperative_id, id),
            CHECK (credit_kobo > 0 OR debit_kobo > 0),
            CHECK (
                (status = 'unmatched' AND matched_payment_id IS NULL
                    AND ignored_note IS NULL AND decided_by IS NULL)
                OR (status = 'suggested' AND matched_payment_id IS NOT NULL
                    AND ignored_note IS NULL AND decided_by IS NULL)
                OR (status = 'matched' AND matched_payment_id IS NOT NULL
                    AND ignored_note IS NULL AND decided_by IS NOT NULL)
                OR (status = 'ignored' AND matched_payment_id IS NULL
                    AND ignored_note IS NOT NULL AND char_length(btrim(ignored_note)) BETWEEN 1 AND 500
                    AND decided_by IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX bank_statement_lines_fingerprint_uq
        ON bank_statement_lines (cooperative_id, fingerprint)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX bank_statement_lines_payment_uq
        ON bank_statement_lines (cooperative_id, matched_payment_id)
        WHERE matched_payment_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX bank_statement_lines_status_idx
        ON bank_statement_lines (cooperative_id, status, line_date DESC, id)
        """
    )
    op.execute(
        """
        CREATE INDEX bank_statement_lines_import_idx
        ON bank_statement_lines (cooperative_id, import_id, line_date, id)
        """
    )
    _tenant_rls("bank_statement_imports")
    _tenant_rls("bank_statement_lines")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON bank_statement_imports TO ajo_app;
            REVOKE DELETE, TRUNCATE ON bank_statement_imports FROM ajo_app;
            GRANT SELECT, INSERT, UPDATE ON bank_statement_lines TO ajo_app;
            REVOKE DELETE, TRUNCATE ON bank_statement_lines FROM ajo_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE bank_statement_lines")
    op.execute("DROP TABLE bank_statement_imports")
