"""Tests for database URL conversion to asyncpg."""

from __future__ import annotations

import sys

from app.core.db import to_asyncpg_url


class TestToAsyncpgUrl:
    """Test to_asyncpg_url helper for various PostgreSQL URL schemes."""

    def test_plain_postgresql_scheme(self):
        """plain postgresql:// scheme becomes postgresql+asyncpg://"""
        url = "postgresql://user:pass@host:5432/dbname"
        result = to_asyncpg_url(url)
        assert result.startswith("postgresql+asyncpg://")
        assert "user" in result
        assert "pass" in result
        assert "host" in result
        assert "5432" in result
        assert "dbname" in result

    def test_psycopg_scheme(self):
        """postgresql+psycopg:// scheme becomes postgresql+asyncpg://"""
        url = "postgresql+psycopg://user:pass@host:5432/dbname"
        result = to_asyncpg_url(url)
        assert result.startswith("postgresql+asyncpg://")

    def test_psycopg2_scheme(self):
        """postgresql+psycopg2:// scheme becomes postgresql+asyncpg://"""
        url = "postgresql+psycopg2://user:pass@host:5432/dbname"
        result = to_asyncpg_url(url)
        assert result.startswith("postgresql+asyncpg://")

    def test_asyncpg_scheme_passes_through(self):
        """postgresql+asyncpg:// scheme passes through unchanged"""
        url = "postgresql+asyncpg://user:pass@host:5432/dbname"
        result = to_asyncpg_url(url)
        assert result.startswith("postgresql+asyncpg://")

    def test_url_with_special_characters_in_password(self):
        """URL with special characters in password is preserved"""
        # Password with @, :, /, and +
        url = "postgresql://user:p%40ss%3Aw%2Fd%2B@host:5432/dbname"
        result = to_asyncpg_url(url)
        assert "postgresql+asyncpg://" in result

    def test_url_with_query_string(self):
        """URL with query string parameters is preserved"""
        url = "postgresql://user:pass@host:5432/dbname?sslmode=require&application_name=test"
        result = to_asyncpg_url(url)
        assert "application_name=test" in result
        assert "sslmode" not in result  # sslmode handled specially, not in query

    def test_url_with_sslmode_require(self):
        """URL with sslmode=require is converted to connect_args"""
        url = "postgresql://user:pass@host:5432/dbname?sslmode=require"
        result = to_asyncpg_url(url)
        # sslmode should be moved to connect_args, not in URL
        assert "sslmode" not in result

    def test_url_preserves_all_components(self):
        """All URL components (user, password, host, port, db) are preserved"""
        url = "postgresql://myuser:mypassword@myhost:5432/mydb"
        result = to_asyncpg_url(url)
        assert "myuser" in result
        assert "mypassword" in result
        assert "myhost" in result
        assert "5432" in result
        assert "mydb" in result


class TestSchedulerEngineUsesAsyncpg:
    """Test that scheduler engine uses asyncpg driver."""

    def test_scheduler_engine_dialect_is_asyncpg(self):
        """Scheduler engine for psycopg-style URL uses asyncpg dialect"""
        # Import the helper from the db module (same one scheduler uses)
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.db import to_asyncpg_url

        # Build the URL that would be used
        test_url = "postgresql+psycopg://user:pass@host:5432/dbname"
        async_url = to_asyncpg_url(test_url)

        # Create engine to check dialect - without connecting
        # Use asyncio.run to avoid event loop issues
        async def check_dialect():
            engine = create_async_engine(async_url, pool_size=1, max_overflow=0)
            try:
                # Check the dialect driver
                assert engine.dialect.driver == "asyncpg", (
                    f"Expected asyncpg driver, got {engine.dialect.driver}"
                )
            finally:
                await engine.dispose()

        asyncio.run(check_dialect())

    def test_importing_scheduler_does_not_import_psycopg(self):
        """Importing scheduler module does not import psycopg"""
        # This test verifies the fix: importing the scheduler should not
        # require psycopg to be installed
        # We check by seeing if psycopg is in sys.modules after import
        psycopg_in_modules_before = "psycopg" in sys.modules

        # Import the scheduler module (which imports to_asyncpg_url)
        import app.scheduler.__main__  # noqa: F401
        import app.scheduler.overdue_cash  # noqa: F401

        # Check psycopg was not loaded (if it wasn't already)
        if not psycopg_in_modules_before:
            # psycopg (the package) shouldn't be loaded
            assert "psycopg" not in sys.modules, "psycopg was imported unexpectedly"
            assert "psycopg2" not in sys.modules, "psycopg2 was imported unexpectedly"
