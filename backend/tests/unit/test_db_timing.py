from app.core.db import (
    _record_new_connection,
    _record_sql,
    begin_request_db_timing,
    end_request_db_timing,
    record_connection_acquire,
)


def test_request_db_timing_counts_sql_connections_and_acquisition(monkeypatch) -> None:
    monkeypatch.setenv("AJO_DB_TIMING", "1")
    token = begin_request_db_timing()
    try:
        _record_sql(0.012)
        _record_sql(0.008)
        _record_new_connection()
        record_connection_acquire(0.004)
        record_connection_acquire(0.006)
    finally:
        timing = end_request_db_timing(token)

    assert timing is not None
    assert timing.sql_statements == 2
    assert timing.database_time_ms == 20.0
    assert timing.new_connections == 1
    assert timing.connection_acquire_time_ms == 10.0
    assert timing.log_fields() == {
        "db_sql_statements": 2,
        "db_time_ms": 20.0,
        "db_new_connections": 1,
        "db_connection_acquire_ms": 10.0,
    }


def test_request_db_timing_is_inactive_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("AJO_DB_TIMING", raising=False)
    token = begin_request_db_timing()
    try:
        _record_sql(1.0)
        _record_new_connection()
        record_connection_acquire(1.0)
    finally:
        timing = end_request_db_timing(token)

    assert timing is None
