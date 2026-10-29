import pytest

from app.core.db import asyncpg_dsn, split_url, to_sqlalchemy_async_url

pytestmark = pytest.mark.unit
DATABASE_URL_EXAMPLE = (
    "postgresql://ajo_app:pw@db.example.invalid/ajo?sslmode=require&channel_binding=require"
)


def test_split_strips_libpq_params():
    clean, ssl = split_url(DATABASE_URL_EXAMPLE)
    assert ssl == "require"
    assert "sslmode" not in clean and "channel_binding" not in clean


def test_sqlalchemy_url_pooled_disables_cache():
    url, args = to_sqlalchemy_async_url(DATABASE_URL_EXAMPLE, disable_prepared_cache=True)
    assert url.startswith("postgresql+asyncpg://")
    assert "prepared_statement_cache_size=0" in url
    assert args["ssl"] == "require" and args["statement_cache_size"] == 0


def test_local_no_ssl():
    _, args = to_sqlalchemy_async_url(
        "postgresql://u:p@localhost/db?sslmode=disable", disable_prepared_cache=False
    )
    assert "ssl" not in args


@pytest.mark.parametrize("sslmode", ["verify-ca", "verify-full"])
def test_certificate_verification_modes_are_rejected(sslmode: str) -> None:
    url = DATABASE_URL_EXAMPLE.replace("sslmode=require", f"sslmode={sslmode}")
    with pytest.raises(ValueError, match=rf"sslmode={sslmode}.*not supported"):
        to_sqlalchemy_async_url(url)


def test_asyncpg_dsn_keeps_sslmode_drops_channel_binding():
    dsn = asyncpg_dsn(DATABASE_URL_EXAMPLE)
    assert "sslmode=require" in dsn and "channel_binding" not in dsn
