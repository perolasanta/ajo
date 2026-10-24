"""Tenant-scoped policy version and loan product operations."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession

_POLICY_FIELDS = (
    "effective_from",
    "salary_basis",
    "deduction_cap_bps",
    "max_active_loans",
    "guarantors_required",
    "min_savings_kobo",
    "loan_to_savings_multiple_x100",
    "interest_method",
    "confirmation_expiry_minutes",
    "otp_validity_seconds",
    "settings",
)


def _require_role(current: CurrentSession, allowed: set[str]) -> str:
    role = next((item for item in current.roles if item in allowed), None)
    if role is None:
        raise Forbidden("This role cannot change cooperative configuration")
    return role


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
    }


async def propose_policy(current: CurrentSession, values: dict[str, Any], reason: str) -> uuid.UUID:
    role = _require_role(current, {"officer", "admin"})
    async with transaction(current.cooperative_id) as session:
        version = await session.scalar(
            text(
                "SELECT coalesce(max(version), 0) + 1 FROM cooperative_policy_versions "
                "WHERE cooperative_id = :cid"
            ),
            {"cid": current.cooperative_id},
        )
        fields = {key: values[key] for key in _POLICY_FIELDS if key in values}
        fields.setdefault("settings", {})
        policy_id = uuid.uuid4()
        await session.execute(
            text("""
            INSERT INTO cooperative_policy_versions
              (id, cooperative_id, version, status, proposed_by, settings,
               effective_from, salary_basis, deduction_cap_bps, max_active_loans,
               guarantors_required, min_savings_kobo, loan_to_savings_multiple_x100,
               interest_method, confirmation_expiry_minutes, otp_validity_seconds)
            VALUES (:id, :cid, :version, 'proposed', :actor, CAST(:settings AS jsonb),
               :effective_from, :salary_basis, :deduction_cap_bps, :max_active_loans,
               :guarantors_required, :min_savings_kobo, :loan_to_savings_multiple_x100,
               :interest_method, :confirmation_expiry_minutes, :otp_validity_seconds)
        """),
            {
                "id": policy_id,
                "cid": current.cooperative_id,
                "version": version,
                "actor": current.user_id,
                **fields,
                "settings": json.dumps(fields["settings"], separators=(",", ":")),
            },
        )
        new_values = {**_jsonable(fields), "version": version, "status": "proposed"}
        await session.execute(
            text("""
            INSERT INTO policy_change_log
              (cooperative_id, policy_version_id, action, actor_user_id, reason, new_values)
            VALUES (:cid, :id, 'proposed', :actor, :reason, CAST(:values AS jsonb))
        """),
            {
                "cid": current.cooperative_id,
                "id": policy_id,
                "actor": current.user_id,
                "reason": reason,
                "values": json.dumps(new_values, separators=(",", ":")),
            },
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=role,
            action="policy_proposed",
            entity_type="cooperative_policy_version",
            entity_id=str(policy_id),
            summary={"version": version},
            channel="api",
            ip=None,
            reason=reason,
        )
    return policy_id


async def approve_policy(current: CurrentSession, policy_id: uuid.UUID, reason: str) -> None:
    role = _require_role(current, {"admin"})
    try:
        async with transaction(current.cooperative_id) as session:
            row = (
                (
                    await session.execute(
                        text("""
                SELECT id, version, status, proposed_by, effective_from FROM cooperative_policy_versions
                 WHERE cooperative_id = :cid AND id = :id FOR UPDATE
            """),
                        {"cid": current.cooperative_id, "id": policy_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFound("Policy version not found")
            if row["status"] != "proposed":
                raise Conflict("Policy version is not awaiting approval")
            if row["proposed_by"] == current.user_id:
                raise Conflict("A different administrator must approve this policy")
            await session.execute(
                text("""
                UPDATE cooperative_policy_versions
                   SET status = 'approved', approved_by = :actor
                 WHERE cooperative_id = :cid AND id = :id
            """),
                {"actor": current.user_id, "cid": current.cooperative_id, "id": policy_id},
            )
            before = {"status": "proposed"}
            after = {"status": "approved", "approved_by": str(current.user_id)}
            await session.execute(
                text("""
                INSERT INTO policy_change_log
                  (cooperative_id, policy_version_id, action, actor_user_id, reason, previous_values, new_values)
                VALUES (:cid, :id, 'approved', :actor, :reason, CAST(:before AS jsonb), CAST(:after AS jsonb))
            """),
                {
                    "cid": current.cooperative_id,
                    "id": policy_id,
                    "actor": current.user_id,
                    "reason": reason,
                    "before": json.dumps(before),
                    "after": json.dumps(after),
                },
            )
            await append_audit(
                session,
                cooperative_id=current.cooperative_id,
                actor_user_id=current.user_id,
                role=role,
                action="policy_approved",
                entity_type="cooperative_policy_version",
                entity_id=str(policy_id),
                summary={"version": row["version"]},
                channel="api",
                ip=None,
                reason=reason,
            )
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23P01":
            raise Conflict("Policy effective dates overlap another approved version") from exc
        raise


async def get_effective_policy(cooperative_id: uuid.UUID, on_date: date) -> dict[str, Any]:
    async with transaction(cooperative_id) as session:
        row = (
            (
                await session.execute(
                    text("""
            SELECT id, version, effective_from, effective_to, salary_basis,
                   deduction_cap_bps, max_active_loans, guarantors_required,
                   min_savings_kobo, loan_to_savings_multiple_x100, interest_method,
                   confirmation_expiry_minutes, otp_validity_seconds, settings
              FROM cooperative_policy_versions
             WHERE cooperative_id = :cid AND status = 'approved'
               AND effective_from <= :on_date
               AND (effective_to IS NULL OR :on_date < effective_to)
             ORDER BY effective_from DESC LIMIT 1
        """),
                    {"cid": cooperative_id, "on_date": on_date},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("No policy is effective on that date")
        return dict(row)


async def supersede_policy(
    current: CurrentSession, policy_id: uuid.UUID, effective_to: date, reason: str
) -> None:
    role = _require_role(current, {"admin"})
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text(
                "SELECT version, effective_from FROM cooperative_policy_versions "
                "WHERE cooperative_id=:cid AND id=:id AND status='approved' FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": policy_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise NotFound("Approved policy version not found")
        if effective_to <= row["effective_from"]:
            raise Conflict("Policy end date must follow its effective start date")
        await session.execute(
            text(
                "UPDATE cooperative_policy_versions SET status='superseded', effective_to=:end "
                "WHERE cooperative_id=:cid AND id=:id"
            ),
            {"end": effective_to, "cid": current.cooperative_id, "id": policy_id},
        )
        await session.execute(
            text(
                "INSERT INTO policy_change_log "
                "(cooperative_id,policy_version_id,action,actor_user_id,reason,previous_values,new_values) "
                "VALUES(:cid,:id,'superseded',:actor,:reason,CAST(:before AS jsonb),CAST(:after AS jsonb))"
            ),
            {
                "cid": current.cooperative_id,
                "id": policy_id,
                "actor": current.user_id,
                "reason": reason,
                "before": json.dumps({"status": "approved", "effective_to": None}),
                "after": json.dumps(
                    {"status": "superseded", "effective_to": effective_to.isoformat()}
                ),
            },
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=role,
            action="policy_superseded",
            entity_type="cooperative_policy_version",
            entity_id=str(policy_id),
            summary={"version": row["version"]},
            channel="api",
            ip=None,
            reason=reason,
        )


async def validate_product_safeguards(
    cooperative_id: uuid.UUID, product: dict[str, Any], on_date: date
) -> None:
    policy = await get_effective_policy(cooperative_id, on_date)
    if product["guarantors_required"] < policy["guarantors_required"]:
        raise Conflict("Loan product cannot require fewer guarantors than cooperative policy")
    if product["interest_method"] != policy["interest_method"]:
        raise Conflict("Loan product interest method must match cooperative policy")


async def create_loan_product(
    current: CurrentSession, product: dict[str, Any], on_date: date, reason: str
) -> uuid.UUID:
    role = _require_role(current, {"officer", "admin"})
    await validate_product_safeguards(current.cooperative_id, product, on_date)
    product_id = uuid.uuid4()
    async with transaction(current.cooperative_id) as session:
        if product.get("supersedes_id") is not None:
            previous = await session.execute(
                text("SELECT code FROM loan_products WHERE cooperative_id=:cid AND id=:id"),
                {"cid": current.cooperative_id, "id": product["supersedes_id"]},
            )
            previous_code = previous.scalar_one_or_none()
            if previous_code is None or previous_code != product["code"]:
                raise Conflict(
                    "A superseding product must reference an existing product with the same code"
                )
        await session.execute(
            text("""
                INSERT INTO loan_products
                  (id, cooperative_id, code, name, interest_method, annual_rate_bps,
                   min_amount_kobo, max_amount_kobo, min_tenor_months, max_tenor_months,
                   fee_bps, guarantors_required, status, supersedes_id)
                VALUES (:id, :cid, :code, :name, :interest_method, :annual_rate_bps,
                   :min_amount_kobo, :max_amount_kobo, :min_tenor_months, :max_tenor_months,
                   :fee_bps, :guarantors_required, 'proposed', :supersedes_id)
            """),
            {
                **product,
                "id": product_id,
                "cid": current.cooperative_id,
                "fee_bps": product.get("fee_bps", 0),
            },
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=role,
            action="loan_product_proposed",
            entity_type="loan_product",
            entity_id=str(product_id),
            summary={"code": product["code"]},
            channel="api",
            ip=None,
            reason=reason,
        )
    return product_id


async def approve_loan_product(
    current: CurrentSession, product_id: uuid.UUID, on_date: date, reason: str
) -> None:
    role = _require_role(current, {"admin"})
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text("""SELECT id, code, name, interest_method, annual_rate_bps,
                          min_amount_kobo, max_amount_kobo, min_tenor_months,
                          max_tenor_months, fee_bps, guarantors_required, status,
                          supersedes_id, approved_at
                     FROM loan_products
                    WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
            {"cid": current.cooperative_id, "id": product_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise NotFound("Loan product not found")
        if row["status"] != "proposed":
            raise Conflict("Loan product is not awaiting approval")
        product = dict(row)
        await validate_product_safeguards(current.cooperative_id, product, on_date)
        if product["supersedes_id"] is not None:
            replaced = await session.execute(
                text(
                    "UPDATE loan_products SET status='superseded' "
                    "WHERE cooperative_id=:cid AND id=:old_id AND status='approved' RETURNING id"
                ),
                {"cid": current.cooperative_id, "old_id": product["supersedes_id"]},
            )
            if replaced.scalar_one_or_none() is None:
                raise Conflict("A superseding product must replace an approved product")
        await session.execute(
            text(
                "UPDATE loan_products SET status='approved', approved_at=now() "
                "WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": current.cooperative_id, "id": product_id},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=role,
            action="loan_product_approved",
            entity_type="loan_product",
            entity_id=str(product_id),
            summary={"code": row["code"]},
            channel="api",
            ip=None,
            reason=reason,
        )


async def set_module_enabled(current: CurrentSession, module: str, enabled: bool) -> None:
    _require_role(current, {"admin"})
    async with transaction(current.cooperative_id) as session:
        await session.execute(
            text("""
            INSERT INTO cooperative_modules (cooperative_id, module, enabled, enabled_at, disabled_at)
            VALUES (:cid, :module, :enabled, now(), CASE WHEN :enabled THEN NULL ELSE now() END)
            ON CONFLICT (cooperative_id, module) DO UPDATE SET
              enabled = EXCLUDED.enabled,
              enabled_at = CASE WHEN EXCLUDED.enabled THEN now() ELSE cooperative_modules.enabled_at END,
              disabled_at = CASE WHEN EXCLUDED.enabled THEN NULL ELSE now() END
        """),
            {"cid": current.cooperative_id, "module": module, "enabled": enabled},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role="admin",
            action="module_updated",
            entity_type="cooperative_module",
            entity_id=module,
            summary={"enabled": enabled},
            channel="api",
            ip=None,
        )
