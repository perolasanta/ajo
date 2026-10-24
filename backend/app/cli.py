"""Operational CLI for cooperative bootstrap and audit verification."""

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

from app.core.db import make_engine
from app.core.security import hash_password
from app.domains.audit.service import append_audit, verify_audit_chain
from app.domains.ledger.chart import create_default_chart

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
            await session.execute(
                text(
                    """
                    INSERT INTO users (id, cooperative_id, email, password_hash)
                    VALUES (:id, :cooperative_id, :email, :password_hash)
                    """
                ),
                {
                    "id": user_id,
                    "cooperative_id": cooperative_id,
                    "email": email,
                    "password_hash": hash_password(temporary_password),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO user_roles (cooperative_id, user_id, role) "
                    "VALUES (:cooperative_id, :user_id, 'admin')"
                ),
                {"cooperative_id": cooperative_id, "user_id": user_id},
            )
            await append_audit(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role="admin",
                action="cooperative_bootstrapped",
                entity_type="user",
                entity_id=str(user_id),
                summary={"role": "admin"},
                channel="cli",
                ip=None,
            )
        return temporary_password
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create-cooperative")
    create_parser.add_argument("--slug", required=True)
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--admin-email", required=True)
    verify_parser = subparsers.add_parser("verify-audit")
    verify_parser.add_argument("--cooperative-id", required=True, type=uuid.UUID)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "create-cooperative":
        temporary_password = await create_cooperative(args.slug, args.name, args.admin_email)
        print(f"Cooperative created: {args.slug}")
        print(f"Admin email: {args.admin_email.strip().lower()}")
        print(f"One-time temporary password: {temporary_password}")
        return 0
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
