import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings, get_settings

pytestmark = pytest.mark.unit


def test_app_env_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None)


@pytest.mark.parametrize("app_env", ["dev", "prod", "test", "invalid"])
def test_unknown_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None)


def test_production_disables_api_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        from app.main import create_app

        app = create_app()
        assert app.docs_url is None
        assert app.openapi_url is None
        assert app.redoc_url is None
    finally:
        get_settings.cache_clear()


def test_default_pool_capacity_fits_neon_free_small_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    settings = Settings(_env_file=None, app_env="development")

    assert (settings.db_pool_size, settings.db_max_overflow) == (10, 10)
    api_and_worker_processes = 2 + 1
    scheduler_connection = 1
    available_connections = 104 - 7
    assert (
        settings.db_pool_size + settings.db_max_overflow
    ) * api_and_worker_processes + scheduler_connection <= available_connections
