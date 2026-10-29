import pytest

from app.core.config import Settings
from app.core.db import validate_application_role_identity

pytestmark = pytest.mark.unit


def test_owner_role_is_rejected_for_application_processes():
    with pytest.raises(RuntimeError, match="must not be the database owner"):
        validate_application_role_identity(
            current_user="neondb_owner",
            is_database_owner=True,
            bypasses_rls=False,
            member_of_bypass_role=False,
        )


def test_bypass_rls_role_is_rejected_for_application_processes():
    with pytest.raises(RuntimeError, match="must not bypass row-level security"):
        validate_application_role_identity(
            current_user="ajo_app",
            is_database_owner=False,
            bypasses_rls=True,
            member_of_bypass_role=False,
        )


def test_scheduler_has_a_dedicated_direct_connection_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    settings = Settings(database_url_scheduler="postgresql://ajo_scheduler:pw@db/ajo")

    assert settings.database_url_scheduler.endswith("/ajo")
