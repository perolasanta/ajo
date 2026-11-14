"""Bank statement CSV parsing and duplicate detection tests."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.auth.service import CurrentSession
from app.domains.bank_statements import service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await connection.fetchval("SELECT current_user") == "ajo_app"
    yield connection
    await connection.close()


def session(cid: uuid.UUID, uid: uuid.UUID, role: str) -> CurrentSession:
    return CurrentSession(
        cid,
        "bank-statement-test",
        uuid.uuid4(),
        uid,
        "staff@example.test",
        (role,),
        bytes(32),
    )


async def test_parse_statement_csv_supports_common_formats_and_preview(db):
    seed = await create_tenant(db, slug_prefix="bankstmt", roles=("officer", "treasurer"))
    cid = seed.cooperative_id
    actor = seed.user_ids[0]
    payload = (
        b"Date,Description,Reference,Credit,Debit\n"
        b"27/02/2024,Salary credit,REF-1,1250.78,\n"
        b"2024-02-28,House rent,REF-2,,1234.00\n"
        b"03-Feb-2024,=cmd|' /C calc',REF-3,650.00,\n"
        b"bad,row\n"
    )
    preview = await service.preview_statement_import(
        session(cid, actor, "officer"),
        content=payload,
        filename="statement.csv",
        column_map={
            "date": "Date",
            "narration": "Description",
            "reference": "Reference",
            "credit": "Credit",
            "debit": "Debit",
        },
    )
    assert preview["total_rows"] == 3
    assert preview["errors"][0]["row"] == 5
    assert preview["preview"][0]["date"] == "2024-02-27"
    assert preview["preview"][0]["credit_kobo"] == 125078
    assert preview["preview"][1]["debit_kobo"] == 123400
    assert preview["preview"][2]["narration"] == "'=cmd|' /C calc'"
    assert preview["preview"][2]["credit_kobo"] == 65000


async def test_import_rejects_duplicate_files_and_skips_overlaps(db):
    seed = await create_tenant(db, slug_prefix="bankstmt-dup", roles=("treasurer", "officer"))
    cid = seed.cooperative_id
    actor = seed.user_ids[0]
    rows = [
        "Date,Narration,Amount,Reference",
        "2024-03-01,Member A transfer,1250.00,REF-A",
        "2024-03-02,Member B transfer,1400.00,REF-B",
    ]
    csv_text = "\n".join(rows) + "\n"
    first = await service.import_statement_file(
        session(cid, actor, "treasurer"),
        content=csv_text.encode(),
        filename="dup.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "amount": "Amount",
            "reference": "Reference",
        },
    )
    assert first["inserted_count"] == 2
    second = await service.import_statement_file(
        session(cid, actor, "treasurer"),
        content=csv_text.encode(),
        filename="dup.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "amount": "Amount",
            "reference": "Reference",
        },
    )
    assert second["skipped_count"] == 2
    assert second["inserted_count"] == 0
    overlap = (
        "\n".join(
            [
                "Date,Narration,Amount,Reference",
                "2024-03-01,Member A transfer,1250.00,REF-A",
                "2024-03-03,New transfer,3000.00,REF-C",
            ]
        )
        + "\n"
    )
    third = await service.import_statement_file(
        session(cid, actor, "treasurer"),
        content=overlap.encode(),
        filename="dup-overlap.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "amount": "Amount",
            "reference": "Reference",
        },
    )
    assert third["inserted_count"] == 1
    assert third["skipped_count"] == 1
