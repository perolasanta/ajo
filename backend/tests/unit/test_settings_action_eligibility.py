from __future__ import annotations

import uuid

from app.domains.notifications import eligibility


def test_settings_actions_are_admin_only_and_exclude_proposer() -> None:
    assert eligibility.POLICY_APPROVAL == "policy_approval"
    assert eligibility.PRODUCT_APPROVAL == "product_approval"
    proposer = uuid.uuid4()
    approver = uuid.uuid4()
    candidates = [
        {
            "event_type": eligibility.POLICY_APPROVAL,
            "entity_id": uuid.uuid4(),
            "excluded_user_ids": [proposer],
        },
        {
            "event_type": eligibility.PRODUCT_APPROVAL,
            "entity_id": uuid.uuid4(),
            "excluded_user_ids": [proposer],
        },
    ]
    assert eligibility.action_items_for_candidate_rows(candidates, proposer, {"admin"}) == {}
    assert set(eligibility.action_items_for_candidate_rows(candidates, approver, {"admin"})) == {
        "settings"
    }
    assert eligibility.action_items_for_candidate_rows(candidates, approver, {"officer"}) == {}
