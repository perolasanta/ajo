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
