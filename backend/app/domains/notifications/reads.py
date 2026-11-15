from __future__ import annotations

from app.core.db import transaction
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility


async def get_action_counts(current: CurrentSession) -> dict[str, dict[str, int]]:
    """Summarize counts from the same action IDs consumed by list filters."""
    async with transaction(current.cooperative_id) as session:
        action_ids = await eligibility.get_action_entity_ids_for_user(
            session, current.cooperative_id, current.user_id, set(current.roles)
        )

    def count(event_type: str) -> int:
        return len(action_ids.get(event_type, ()))

    roles = set(current.roles)
    summary: dict[str, dict[str, int]] = {}
    if "officer" in roles or "treasurer" in roles:
        summary["payments"] = {"awaiting_confirmation": count(eligibility.PAYMENT_CONFIRMATION)}
    if "treasurer" in roles:
        summary["payroll"] = {"awaiting_posting": count(eligibility.PAYROLL_POSTING)}
        summary["disbursements"] = {"ready_to_record": count(eligibility.DISBURSEMENT_RECORD)}
        summary["loans"] = {"disputed_or_paused": count(eligibility.LOAN_DISPUTED)}
        summary["withdrawal_requests"] = {
            "awaiting_approval": count(eligibility.WITHDRAWAL_APPROVAL)
        }
        summary["share_purchases"] = {
            "awaiting_approval": count(eligibility.SHARE_PURCHASE_APPROVAL)
        }
        summary.setdefault("payments", {})["awaiting_deposit"] = count(eligibility.CASH_DEPOSIT)
    if "admin" in roles:
        summary.setdefault("payroll", {})["awaiting_sign_off"] = count(eligibility.PAYROLL_SIGN_OFF)
        disbursements = summary.setdefault("disbursements", {})
        disbursements["awaiting_authorization"] = count(eligibility.DISBURSEMENT_AUTHORIZATION)
        disbursements["awaiting_confirmation"] = count(eligibility.DISBURSEMENT_CONFIRMATION)
        summary["role_changes"] = {"awaiting_approval": count(eligibility.ROLE_CHANGE_APPROVAL)}
        summary["settings"] = {
            "policy_approval": count(eligibility.POLICY_APPROVAL),
            "product_approval": count(eligibility.PRODUCT_APPROVAL),
        }
    if "committee" in roles:
        summary.setdefault("loans", {})["awaiting_decision"] = count(eligibility.LOAN_DECISION)
    return summary
