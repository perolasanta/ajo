"""Contracts for share-purchase request and maker-checker behavior."""

from __future__ import annotations

import inspect

from app.api import share_purchases
from app.domains.notifications import eligibility
from app.domains.share_purchases import service


def test_share_purchase_action_requires_treasurer_and_excludes_requester() -> None:
    spec = eligibility.ACTION_SPECS[eligibility.SHARE_PURCHASE_APPROVAL]
    assert spec.roles == frozenset({"treasurer"})
    assert "approval" in spec.action_kind
    sql = eligibility._CANDIDATE_SQL[eligibility.SHARE_PURCHASE_APPROVAL]
    assert "r.requested_by" in sql
    assert "r.status='pending'" in sql


def test_share_purchase_api_exposes_request_and_all_decisions() -> None:
    assert hasattr(share_purchases, "request_share_purchase")
    assert hasattr(share_purchases, "approve_share_purchase")
    assert hasattr(share_purchases, "reject_share_purchase")
    assert hasattr(share_purchases, "cancel_share_purchase")
    assert "note" in share_purchases.SharePurchaseDecisionBody.model_fields


def test_approval_posts_one_atomic_ledger_entry() -> None:
    source = inspect.getsource(service.approve_purchase)
    assert "FOR UPDATE" in source
    assert "post_journal_entry" in source
    assert "member_savings" in source
    assert "member_shares" in source
    assert "savings are insufficient" in source
