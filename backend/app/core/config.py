"""Application settings, loaded from environment variables (see /.env.example)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: Literal["development", "staging", "production"]
    app_name: str = "ajo"
    log_level: str = "INFO"
    git_sha: str = "dev"

    # Pooled URL (-pooler host, role ajo_app): API and worker.
    database_url: str = Field(default="")
    # Direct URL (owner role): one-shot Alembic migrations and pg_dump only.
    database_url_direct: str = Field(default="")
    # Direct URL (ajo_scheduler role): the scheduler's session advisory lock only.
    database_url_scheduler: str = Field(default="")
    database_url_readonly: str = Field(default="")

    db_disable_prepared_cache: bool = True
    db_pool_size: int = Field(default=10, ge=1, le=10)
    db_max_overflow: int = Field(default=10, ge=0, le=10)

    secret_key: str = "change-me"  # noqa: S105 (rejected in production by the validator below)
    encryption_key: str = ""
    session_idle_minutes: int = Field(default=30, gt=0)
    session_absolute_hours: int = Field(default=12, gt=0)
    login_max_attempts: int = Field(default=5, gt=0)
    login_lockout_minutes: int = Field(default=15, gt=0)
    login_rate_limit_attempts: int = Field(default=20, gt=0)
    login_rate_limit_window_minutes: int = Field(default=5, gt=0)
    trusted_proxy_ips: str = ""
    telegram_enabled: bool = False
    telegram_bot_username: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    sentry_dsn: str = ""

    public_base_url: str = ""
    sms_enabled: bool = False
    sms_gateway_url: str = ""
    sms_gateway_api_key: SecretStr = SecretStr("")
    sms_sender_id: str = ""
    email_enabled: bool = False
    brevo_api_key: SecretStr = SecretStr("")
    email_from_address: str = ""
    email_from_name: str = ""
    email_provider: str = "brevo_api"
    smtp_host: str = ""
    smtp_port: str = ""
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: str = ""
    sms_request_timeout_seconds: float = Field(default=10.0, gt=0)
    outbox_poll_interval_seconds: float = Field(default=2.0, gt=0)
    outbox_claim_lease_seconds: int = Field(default=120, gt=0)
    outbox_max_attempts: int = Field(default=5, gt=0)
    outbox_retry_base_seconds: int = Field(default=10, gt=0)
    outbox_retry_max_seconds: int = Field(default=3600, gt=0)
    outbox_batch_size_per_tenant: int = Field(default=25, gt=0)

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env in {"staging", "production"}:
            public_base_url = self.public_base_url
            error = (
                "PUBLIC_BASE_URL must be a valid absolute HTTPS URL without credentials, "
                "query, or fragment in staging or production"
            )
            try:
                parsed_url = urlsplit(public_base_url.rstrip("/"))
                _ = parsed_url.port
            except ValueError as exc:
                raise ValueError(error) from exc
            if (
                not public_base_url
                or public_base_url != public_base_url.strip()
                or any(character.isspace() for character in public_base_url)
                or parsed_url.scheme != "https"
                or not parsed_url.netloc
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ValueError(error)
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
