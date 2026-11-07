"""Failing contract tests for read-only member detail tabs."""
from __future__ import annotations

import uuid

import pytest

from app.domains.auth.service import CurrentSession
from app.domains.members import reads


MEMBER_ID = uuid.uuid4()
COOPERATIVE_ID = uuid.uuid4()


def current(*roles: str) -> CurrentSession:
    return CurrentSession(
        cooperative_id=COOPERATIVE_ID,
        slug="test-cooperative",
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        email="reader@example.test",
        roles=roles,
        csrf_hash=bytes(32),
    )


def test_tab_visibility_defaults_are_role_specific() -> None:
    assert reads.tab_access("admin") == {"overview", "statement", "loans", "activity"}
    assert reads.tab_access("officer") == {"overview", "statement", "loans"}
    assert reads.tab_access("treasurer") == {"overview", "statement", "loans"}
    assert reads.tab_access("auditor") == {"overview", "statement", "loans", "activity"}
    assert reads.tab_access("committee") == {"overview", "loans"}


def test_running_balance_is_the_ledger_total_in_newest_first_rows() -> None:
    rows = [
        {"amount_kobo": -300, "running_balance_kobo": 700},
        {"amount_kobo": 1000, "running_balance_kobo": 1000},
    ]
    assert reads.validate_running_balances(rows) == 700


def test_statement_query_is_tenant_scoped_and_paginated() -> None:
    sql = str(reads.STATEMENT_QUERY).lower()
    assert "cooperative_id=:cid" in sql
    assert "member_id=:mid" in sql
    assert "limit :limit offset :offset" in sql
    assert "row_number" in sql or "sum(" in sql


@pytest.mark.parametrize("role", ["committee", "member", "unknown"])
def test_restricted_tabs_are_not_readable(role: str) -> None:
    with pytest.raises(Exception):
        reads.require_tab(role, "statement")


def test_activity_query_is_tenant_scoped_and_member_filtered() -> None:
    sql = str(reads.ACTIVITY_QUERY).lower()
    assert "cooperative_id=:cid" in sql
    assert "member_id" in sql
    assert "limit :limit offset :offset" in sql


def test_member_tab_response_contract_has_no_bank_account_number() -> None:
    forbidden = {"account_number", "account_number_encrypted", "full_account_number"}
    assert forbidden.isdisjoint(reads.PUBLIC_TAB_FIELDS)
