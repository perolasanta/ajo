from app.core.db import asyncpg_dsn, split_url, to_sqlalchemy_async_url

NEON = "postgresql://ajo_app:pw@ep-cool-123-pooler.eu-central-1.aws.neon.tech/ajo?sslmode=require&channel_binding=require"


def test_split_strips_libpq_params():
    clean, ssl = split_url(NEON)
    assert ssl == "require"
    assert "sslmode" not in clean and "channel_binding" not in clean


def test_sqlalchemy_url_pooled_disables_cache():
    url, args = to_sqlalchemy_async_url(NEON, disable_prepared_cache=True)
    assert url.startswith("postgresql+asyncpg://")
    assert "prepared_statement_cache_size=0" in url
    assert args["ssl"] == "require" and args["statement_cache_size"] == 0


def test_local_no_ssl():
    _, args = to_sqlalchemy_async_url(
        "postgresql://u:p@localhost/db?sslmode=disable", disable_prepared_cache=False
    )
    assert "ssl" not in args


def test_asyncpg_dsn_keeps_sslmode_drops_channel_binding():
    dsn = asyncpg_dsn(NEON)
    assert "sslmode=require" in dsn and "channel_binding" not in dsn
