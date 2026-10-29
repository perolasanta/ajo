"""Application settings, loaded from environment variables (see /.env.example)."""

from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: Literal["development", "staging", "production"]
    app_name: str = "ajo"
    log_level: str = "INFO"
    git_sha: str = "dev"

    # Pooled URL (-pooler host, role ajo_app): API and worker.
    database_url: str = Field(default="")
    # Direct URL (owner role): Alembic, scheduler, pg_dump.
    database_url_direct: str = Field(default="")
    database_url_readonly: str = Field(default="")

    db_disable_prepared_cache: bool = True
    db_pool_size: int = 5
    db_max_overflow: int = 5

    secret_key: str = "change-me"  # noqa: S105 (rejected in production by the validator below)
    encryption_key: str = ""
    session_idle_minutes: int = Field(default=30, gt=0)
    session_absolute_hours: int = Field(default=12, gt=0)
    login_max_attempts: int = Field(default=5, gt=0)
    login_lockout_minutes: int = Field(default=15, gt=0)
    login_rate_limit_attempts: int = Field(default=20, gt=0)
    login_rate_limit_window_minutes: int = Field(default=5, gt=0)
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    sentry_dsn: str = ""

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production" and self.secret_key in ("", "change-me"):
            raise ValueError("SECRET_KEY must be set to a long random value in production")
        if self.encryption_key:
            try:
                Fernet(self.encryption_key.encode())
            except (ValueError, TypeError) as exc:
                raise ValueError("ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.app_env == "production" and self.encryption_key in (
            "",
            "CHANGE_ME",
            "CHANGE_ME_GENERATE_WITH_FERNET_GENERATE_KEY",
        ):
            raise ValueError("ENCRYPTION_KEY must be set to a generated Fernet key in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
