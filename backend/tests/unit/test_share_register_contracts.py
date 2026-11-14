"""Contracts for the read-only share register access and ledger projection."""

from __future__ import annotations

import inspect
import uuid

import pytest

from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.share_purchases import reads


def session(*roles: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "share-register-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "staff@example.test",
        roles,
        bytes(32),
    )


@pytest.mark.parametrize("role", ["admin", "treasurer", "officer", "auditor", "committee"])
def test_share_register_allows_only_the_requested_read_roles(role: str) -> None:
    reads.require_register_role(session(role))


@pytest.mark.parametrize("role", ["member", "operator"])
def test_share_register_denies_other_roles(role: str) -> None:
    with pytest.raises(Forbidden, match="share register"):
        reads.require_register_role(session(role))


def test_share_register_projection_uses_ledger_and_money_name_fallbacks() -> None:
    source = "\n".join(
        (reads._SUMMARY_QUERY, reads._TOTAL_QUERY, reads._REGISTER_QUERY)
    ) + inspect.getsource(reads.list_share_register)
    assert "member_shares" in source
    assert "journal_lines" in source
    assert "share_purchase_requests" in source
    assert "Unknown user" in source
    assert "Unnamed user" in source
    assert "ILIKE" in source


def test_share_register_queries_use_identical_ctes() -> None:
    queries = (reads._SUMMARY_QUERY, reads._TOTAL_QUERY, reads._REGISTER_QUERY)
    cte_blocks = [query.rsplit("\n)\n", 1)[0] + "\n)\n" for query in queries]
    assert cte_blocks[0] == cte_blocks[1] == cte_blocks[2]
