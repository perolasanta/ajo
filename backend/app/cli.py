"""Operational CLI for cooperative bootstrap, demo seeding and audit verification."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, transaction
from app.domains.audit.service import verify_audit_chain
from app.domains.auth.service import resolve_tenant_slug
from app.domains.auth.staff import _create_staff_user_in_session
from app.domains.ledger.chart import create_default_chart
from app.seed_demo import seed_demo

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _direct_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    url = os.environ.get("DATABASE_URL_DIRECT", "")
    parsed = urlparse(url)
    if not url or parsed.username == "ajo_app" or "-pooler" in (parsed.hostname or ""):
        raise RuntimeError("DATABASE_URL_DIRECT must be the direct owner connection")
    return url


async def create_cooperative(slug: str, name: str, admin_email: str) -> str:
    if not _SLUG.fullmatch(slug):
        raise ValueError("slug must be lowercase letters, digits, and single hyphens")
    email = admin_email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("admin email is invalid")

    engine = make_engine(_direct_url(), pooled=False, application_name="ajo-bootstrap")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    cooperative_id = uuid.uuid4()
    user_id = uuid.uuid4()
    temporary_password = secrets.token_urlsafe(18)
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.cooperative_id', :cooperative_id, true)"),
                {"cooperative_id": str(cooperative_id)},
            )
            await session.execute(
                text("INSERT INTO cooperatives (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": cooperative_id, "name": name, "slug": slug},
            )
            await create_default_chart(session, cooperative_id)
            await session.execute(
                text(
                    "INSERT INTO tenant_directory (slug, cooperative_id) "
                    "VALUES (:slug, :cooperative_id)"
                ),
                {"slug": slug, "cooperative_id": cooperative_id},
            )
            await _create_staff_user_in_session(
                session,
                cooperative_id=cooperative_id,
                email=email,
                password=temporary_password,
                roles=("admin",),
                actor_user_id=user_id,
                actor_role="admin",
                channel="cli",
                user_id=user_id,
            )
        return temporary_password
    finally:
        await engine.dispose()


async def list_dead_notifications(slug: str) -> int:
    """List dead-lettered notifications for a slug without their payloads."""
    tenant = await resolve_tenant_slug(slug)
    if tenant is None:
        print("slug does not exist", file=sys.stderr)
        return 1
    cooperative_id = tenant[0]
    async with transaction(cooperative_id) as session:
        rows = (
            await session.execute(
                text(
                    """SELECT id, member_id, kind, attempts, created_at,
                              next_attempt_at, sent_at
                         FROM notifications
                        WHERE cooperative_id = :cid AND status = 'failed'
                        ORDER BY created_at"""
                ),
                {"cid": cooperative_id},
            )
        ).all()
    if not rows:
        print("No dead-lettered notifications.")
        return 0
    header = (
        f"{'id':36}  {'member_id':36}  {'kind':20}  {'attempts':>8}  "
        f"{'created_at':25}  {'next_attempt_at':25}  {'sent_at':25}"
    )
    print(header)
    for row in rows:
        sent = "" if row.sent_at is None else str(row.sent_at)
        print(
            f"{str(row.id):36}  {str(row.member_id):36}  {str(row.kind):20}  "
            f"{int(row.attempts):>8}  {str(row.created_at):25}  "
            f"{str(row.next_attempt_at):25}  {sent:25}"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create-cooperative")
    create_parser.add_argument("--slug", required=True)
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--admin-email", required=True)
    verify_parser = subparsers.add_parser("verify-audit")
    verify_parser.add_argument("--cooperative-id", required=True, type=uuid.UUID)
    seed_parser = subparsers.add_parser("seed-demo")
    seed_parser.add_argument("--slug", required=True)
    seed_parser.add_argument(
        "--yes-fake-data", action="store_true", help="confirm seeding of fictional demo data"
    )
    subparsers.add_parser("notifications-dead").add_argument("--slug", required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "create-cooperative":
        temporary_password = await create_cooperative(args.slug, args.name, args.admin_email)
        print(f"Cooperative created: {args.slug}")
        print(f"Admin email: {args.admin_email.strip().lower()}")
        print(f"One-time temporary password: {temporary_password}")
        return 0
    if args.command == "seed-demo":
        try:
            await seed_demo(args.slug, args.yes_fake_data)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.command == "notifications-dead":
        return await list_dead_notifications(args.slug)
    verification = await verify_audit_chain(args.cooperative_id)
    if verification.valid:
        print(f"Audit chain valid ({verification.entries_checked} entries)")
        return 0
    print(
        f"Audit chain invalid at entry {verification.first_invalid_id} "
        f"after {verification.entries_checked - 1} valid entries",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
