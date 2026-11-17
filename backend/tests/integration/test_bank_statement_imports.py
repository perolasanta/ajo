"""Bank statement CSV parsing and duplicate detection tests."""

from __future__ import annotations

import json
import uuid
from datetime import date

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.auth import current_session
from app.core.db import asyncpg_dsn, dispose_engine, transaction
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.bank_statements import service
from app.domains.payments import service as payment_service
from app.main import create_app
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


@pytest.fixture
async def statement_api():
    selected: dict[str, CurrentSession] = {}
    app = create_app()
    app.dependency_overrides[current_session] = lambda: selected["current"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://bank-statement.test"
    ) as client:
        yield client, selected
    app.dependency_overrides.clear()


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


async def test_http_preview_import_and_list_use_browser_multipart_mapping(
    db: asyncpg.Connection,
    statement_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(db, slug_prefix="bankstmt-http")
    client, selected = statement_api
    selected["current"] = session(seed.cooperative_id, seed.user_ids[0], "officer")
    csrf = "bank-statement-csrf"
    client.cookies.set("ajo_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    payload = (
        b"Date,Narration,Reference,Credit,Debit\n"
        b"2024-03-01,Member contribution,REF-001,1250.00,\n"
        b"2024-03-02,,REF-002,250.00,\n"
        b"2024-03-03,Member savings,REF-003,100.00,\n"
        b"2024-03-04,Monthly fee,REF-004,,20.00\n"
        b"2024-03-05,Member contribution,REF-005,375.00,\n"
        b"2024-03-06,,REF-006,,30.00\n"
        b"2024-03-07,Share purchase,REF-007,500.00,\n"
        b"2024-03-08,Member savings,REF-008,750.00,\n"
    )
    column_map = {
        "date": "Date",
        "narration": "Narration",
        "reference": "Reference",
        "credit": "Credit",
        "debit": "Debit",
    }

    preview = await client.post(
        "/api/v1/bank-statements/preview",
        headers=headers,
        data={"mapping": json.dumps(column_map)},
        files={"file": ("statement.csv", payload, "text/csv")},
    )

    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["total_rows"] == 8
    assert preview_body["errors"] == []

    imported = await client.post(
        "/api/v1/bank-statements/import",
        headers=headers,
        data={"mapping": json.dumps(column_map)},
        files={"file": ("statement.csv", payload, "text/csv")},
    )

    assert imported.status_code == 200, imported.text
    import_body = imported.json()
    assert import_body["inserted_count"] == 8
    assert import_body["skipped_count"] == 0
    assert import_body["errors"] == []
    counts = await db.fetchrow(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE credit_kobo > 0) AS credits,
                  count(*) FILTER (WHERE debit_kobo > 0) AS debits
             FROM bank_statement_lines WHERE cooperative_id=$1""",
        seed.cooperative_id,
    )
    assert counts["total"] == 8
    assert counts["credits"] == 6
    assert counts["debits"] == 2

    repeated = await client.post(
        "/api/v1/bank-statements/import",
        headers=headers,
        data={"mapping": json.dumps(column_map)},
        files={"file": ("statement.csv", payload, "text/csv")},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["inserted_count"] == 0
    assert repeated.json()["skipped_count"] == 8

    listed = await client.get("/api/v1/bank-statements?page=1&page_size=25")

    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 8
    assert "Member contribution" in {item["narration"] for item in listed.json()["items"]}

    ignored = await client.get("/api/v1/bank-statements?status=ignored")
    unmatched = await client.get("/api/v1/bank-statements?status=unmatched")
    assert ignored.status_code == 200, ignored.text
    assert unmatched.status_code == 200, unmatched.text
    assert ignored.json()["total"] == 2
    assert unmatched.json()["total"] == 0
    assert all(item["status"] == "ignored" for item in ignored.json()["items"])
    assert all(item["debit_kobo"] > 0 for item in ignored.json()["items"])


async def test_http_import_saves_identical_blank_narration_rows_in_one_file(
    db: asyncpg.Connection,
    statement_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(db, slug_prefix="bankstmt-blank-duplicates")
    client, selected = statement_api
    selected["current"] = session(seed.cooperative_id, seed.user_ids[0], "officer")
    csrf = "bank-statement-blank-duplicates-csrf"
    client.cookies.set("ajo_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    payload = (
        b"Date,Narration,Amount,Reference\n"
        b"2024-03-01,,250.00,REF-EMPTY\n"
        b"2024-03-01,,250.00,REF-EMPTY\n"
    )
    column_map = {
        "date": "Date",
        "narration": "Narration",
        "amount": "Amount",
        "reference": "Reference",
    }

    preview = await client.post(
        "/api/v1/bank-statements/preview",
        headers=headers,
        data={"mapping": json.dumps(column_map)},
        files={"file": ("blank-duplicates.csv", payload, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["total_rows"] == 2
    assert preview.json()["errors"] == []

    imported = await client.post(
        "/api/v1/bank-statements/import",
        headers=headers,
        data={"mapping": json.dumps(column_map)},
        files={"file": ("blank-duplicates.csv", payload, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["inserted_count"] == 2
    assert imported.json()["skipped_count"] == 0
    assert (
        await db.fetchval(
            "SELECT count(*) FROM bank_statement_lines WHERE cooperative_id=$1 AND narration=''",
            seed.cooperative_id,
        )
        == 2
    )


async def test_every_bank_statement_endpoint_enforces_role_matrix(
    db: asyncpg.Connection,
    statement_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(
        db,
        slug_prefix="bankstmt-role-matrix",
        roles=("officer", "committee", "treasurer", "admin", "auditor"),
    )
    client, selected = statement_api
    line_id = str(uuid.uuid4())
    csrf = "bank-statement-role-matrix-csrf"
    client.cookies.set("ajo_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    read_roles = {"officer", "treasurer", "admin", "auditor"}
    write_roles = {"officer", "treasurer"}
    endpoints = [
        ("GET", "/api/v1/bank-statements", read_roles),
        ("GET", "/api/v1/bank-statements/lines", read_roles),
        ("GET", f"/api/v1/bank-statements/lines/{line_id}", read_roles),
        ("GET", f"/api/v1/bank-statements/{line_id}", read_roles),
        ("GET", "/api/v1/payments/bank-statements/lines", read_roles),
        ("GET", f"/api/v1/payments/bank-statements/lines/{line_id}", read_roles),
        ("POST", "/api/v1/bank-statements/preview", write_roles),
        ("POST", "/api/v1/bank-statements/import", write_roles),
        ("POST", "/api/v1/bank-statements/match", write_roles),
        ("POST", f"/api/v1/bank-statements/{line_id}/accept", write_roles),
        ("POST", f"/api/v1/bank-statements/{line_id}/ignore", write_roles),
        ("POST", f"/api/v1/bank-statements/{line_id}/undo", write_roles),
        ("POST", "/api/v1/payments/bank-statements/preview", write_roles),
        ("POST", "/api/v1/payments/bank-statements/import", write_roles),
        ("POST", "/api/v1/payments/bank-statements/match", write_roles),
    ]
    mapping = {"date": "Date", "narration": "Narration", "amount": "Amount"}
    payload = b"Date,Narration,Amount\n2024-03-01,Role matrix deposit,1.00\n"

    for role in ("officer", "treasurer", "admin", "auditor", "committee", "member"):
        selected["current"] = session(seed.cooperative_id, seed.user_ids[0], role)
        for method, path, allowed_roles in endpoints:
            if method == "GET":
                response = await client.get(path)
            elif path.endswith(("/preview", "/import")):
                response = await client.post(
                    path,
                    headers=headers,
                    data={"mapping": json.dumps(mapping)},
                    files={"file": ("role-matrix.csv", payload, "text/csv")},
                )
            elif path.endswith("/accept"):
                response = await client.post(
                    path,
                    headers=headers,
                    json={"idempotency_key": f"role-matrix-{role}"},
                )
            elif path.endswith("/ignore"):
                response = await client.post(
                    path, headers=headers, json={"note": "role matrix test"}
                )
            else:
                response = await client.post(path, headers=headers)

            if role in allowed_roles:
                assert response.status_code in {200, 404}, (
                    f"{method} {path} for {role}: {response.status_code} {response.text}"
                )
            else:
                assert response.status_code == 403, (
                    f"{method} {path} for {role}: {response.status_code} {response.text}"
                )


async def test_statement_import_succeeds_with_restricted_ajo_app_role(
    db: asyncpg.Connection,
) -> None:
    assert await db.fetchval("SELECT current_user") == "ajo_app"
    seed = await create_tenant(db, slug_prefix="bankstmt-app-role")
    current = session(seed.cooperative_id, seed.user_ids[0], "officer")

    async with transaction(seed.cooperative_id) as session_db:
        role = await session_db.execute(text("SELECT current_user"))
        assert role.scalar_one() == "ajo_app"

    imported = await service.import_statement_file(
        current,
        content=b"Date,Narration,Amount\n2024-03-01,Restricted-role deposit,1250.00\n",
        filename="restricted-role.csv",
        column_map={"date": "Date", "narration": "Narration", "amount": "Amount"},
    )

    assert imported["inserted_count"] == 1
    assert (
        await db.fetchval(
            "SELECT count(*) FROM bank_statement_lines WHERE cooperative_id=$1", seed.cooperative_id
        )
        == 1
    )


async def test_bank_statement_tables_have_app_grants_and_forced_tenant_rls(
    db: asyncpg.Connection,
) -> None:
    table_names = ("bank_statement_imports", "bank_statement_lines")
    for table_name in table_names:
        privileges = await db.fetchrow(
            """SELECT has_table_privilege(current_user, $1::text, 'SELECT') AS can_select,
                      has_table_privilege(current_user, $1::text, 'INSERT') AS can_insert,
                      has_table_privilege(current_user, $1::text, 'UPDATE') AS can_update""",
            f"public.{table_name}",
        )
        assert privileges["can_select"]
        assert privileges["can_insert"]
        assert privileges["can_update"]

        rls = await db.fetchrow(
            """SELECT relrowsecurity, relforcerowsecurity
                 FROM pg_class WHERE oid = to_regclass($1)""",
            f"public.{table_name}",
        )
        assert rls["relrowsecurity"]
        assert rls["relforcerowsecurity"]

    policies = await db.fetch(
        """SELECT tablename, cmd, qual, with_check FROM pg_policies
             WHERE schemaname='public' AND tablename = ANY($1::text[])""",
        list(table_names),
    )
    assert {row["tablename"] for row in policies} == set(table_names)
    assert all(row["cmd"] == "ALL" for row in policies)
    assert all(row["qual"] and row["with_check"] for row in policies)
    assert all("app_current_cooperative_id" in row["with_check"] for row in policies)


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


async def test_import_keeps_line_dates_as_date_objects(db):
    seed = await create_tenant(db, slug_prefix="bankstmt-dates", roles=("treasurer", "officer"))
    cid = seed.cooperative_id
    actor = seed.user_ids[0]
    payload = (
        b"Date,Narration,Amount\n"
        b"27/02/2024,Salary credit,1250.00\n"
        b"2024-02-28,House rent,1200.00\n"
        b"03-Feb-2024,School fees,230.00\n"
    )
    result = await service.import_statement_file(
        session(cid, actor, "treasurer"),
        content=payload,
        filename="dates.csv",
        column_map={"date": "Date", "narration": "Narration", "amount": "Amount"},
    )
    assert result["inserted_count"] == 3
    rows = await db.fetch(
        "SELECT line_date FROM bank_statement_lines WHERE cooperative_id=$1 ORDER BY line_date",
        cid,
    )
    assert [row["line_date"] for row in rows] == [
        date(2024, 2, 3),
        date(2024, 2, 27),
        date(2024, 2, 28),
    ]


async def test_match_statement_lines_classifies_exact_likely_and_ambiguous_matches(db):
    seed = await create_tenant(
        db,
        slug_prefix="bankstmt-match",
        roles=("treasurer", "officer"),
        include_member=True,
    )
    cid = seed.cooperative_id
    actor = seed.user_ids[0]
    member_id = seed.member_id
    current = session(cid, actor, "treasurer")

    await payment_service.record_payment(
        current,
        member_id=member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=125000,
        provider="manual",
        provider_reference="REF-123",
        received_on=date(2024, 3, 1),
        idempotency_key="match-exact",
    )
    likely = await payment_service.record_payment(
        current,
        member_id=member_id,
        channel="bank_transfer",
        purpose="dues",
        amount_kobo=150000,
        provider="manual",
        provider_reference="LIKELY-001",
        received_on=date(2024, 3, 4),
        idempotency_key="match-likely",
    )
    await payment_service.record_payment(
        current,
        member_id=member_id,
        channel="bank_transfer",
        purpose="shares",
        amount_kobo=180000,
        provider="manual",
        provider_reference="AMB-1",
        received_on=date(2024, 3, 4),
        idempotency_key="match-ambiguous-a",
    )
    await payment_service.record_payment(
        current,
        member_id=member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=180000,
        provider="manual",
        provider_reference="AMB-2",
        received_on=date(2024, 3, 5),
        idempotency_key="match-ambiguous-b",
    )
    await payment_service.record_payment(
        current,
        member_id=member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=200000,
        provider="manual",
        provider_reference="OUT-OF-WINDOW",
        received_on=date(2024, 3, 12),
        idempotency_key="match-outside-window",
    )

    csv_text = (
        "Date,Narration,Amount,Reference\n"
        "2024-03-01,Transfer for REF-123,1250.00,REF-123\n"
        "2024-03-05,Transfer from family savings,1500.00,\n"
        "2024-03-03,Transfer from member name,1800.00,AMB-1\n"
        "2024-03-09,Transfer with no match,900.00,REF-Z\n"
        "2024-03-02,Transfer with one kobo off,1250.01,REF-ONE-KOBO-OFF\n"
        "2024-03-20,Outside window amount,2000.00,OUT-OF-WINDOW\n"
        "2024-03-01,Duplicate exact line,1250.00,REF-123\n"
    )
    imported = await service.import_statement_file(
        current,
        content=csv_text.encode(),
        filename="match.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "amount": "Amount",
            "reference": "Reference",
        },
    )
    assert imported["inserted_count"] == 7

    result = await service.match_statement_lines(current)
    assert result["updated_count"] == 2
    assert result["matched_count"] == 2

    rows = await db.fetch(
        "SELECT id, status, matched_payment_id, line_date, narration FROM bank_statement_lines WHERE cooperative_id=$1 ORDER BY line_date, narration",
        cid,
    )
    suggested = [row for row in rows if row["status"] == "suggested"]
    assert len(suggested) == 2
    assert any(row["narration"] == "Transfer from family savings" for row in suggested)
    exact_set = {"Transfer for REF-123", "Duplicate exact line"}
    assert len({row["narration"] for row in suggested}.intersection(exact_set)) == 1
    assert all(
        row["status"] == "unmatched"
        for row in rows
        if row["narration"] not in {"Transfer from family savings"}
        and row["narration"] not in exact_set
    )

    exact_rows = await db.fetch(
        "SELECT matched_payment_id FROM bank_statement_lines WHERE cooperative_id=$1 AND narration = ANY($2)",
        cid,
        ["Transfer for REF-123", "Duplicate exact line"],
    )
    assert sum(1 for row in exact_rows if row["matched_payment_id"] is not None) == 1
    likely_row = await db.fetchrow(
        "SELECT matched_payment_id FROM bank_statement_lines WHERE cooperative_id=$1 AND narration=$2",
        cid,
        "Transfer from family savings",
    )
    assert likely_row is not None and str(likely_row["matched_payment_id"]) == str(likely["id"])
    ambiguous_row = await db.fetchrow(
        "SELECT status, matched_payment_id FROM bank_statement_lines WHERE cooperative_id=$1 AND narration=$2",
        cid,
        "Transfer from member name",
    )
    assert ambiguous_row["status"] == "unmatched"
    assert ambiguous_row["matched_payment_id"] is None


async def test_match_statement_lines_is_tenant_scoped(db):
    seed = await create_tenant(
        db,
        slug_prefix="bankstmt-match-other",
        roles=("treasurer", "officer"),
        include_member=True,
    )
    other = await create_tenant(
        db,
        slug_prefix="bankstmt-match-tenant-b",
        roles=("treasurer", "officer"),
        include_member=True,
    )
    current = session(seed.cooperative_id, seed.user_ids[0], "treasurer")
    other_current = session(other.cooperative_id, other.user_ids[0], "treasurer")

    await payment_service.record_payment(
        other_current,
        member_id=other.member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=125000,
        provider="manual",
        provider_reference="REF-OTHER",
        received_on=date(2024, 3, 2),
        idempotency_key="match-tenant-other",
    )

    csv_text = (
        "Date,Narration,Amount,Reference\n2024-03-01,Transfer for REF-OTHER,1250.00,REF-OTHER\n"
    )
    imported = await service.import_statement_file(
        current,
        content=csv_text.encode(),
        filename="tenant-isolation.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "amount": "Amount",
            "reference": "Reference",
        },
    )
    assert imported["inserted_count"] == 1

    result = await service.match_statement_lines(current)
    assert result["updated_count"] == 0
    assert result["matched_count"] == 0


async def test_accept_statement_line_confirms_recommended_payment_and_blocks_recorder(db):
    seed = await create_tenant(
        db,
        slug_prefix="bankstmt-accept",
        roles=("officer", "treasurer"),
        include_member=True,
    )
    cid = seed.cooperative_id
    officer = seed.user_ids[0]
    treasurer = seed.user_ids[1]
    member_id = seed.member_id
    assert member_id is not None
    for code, name, kind in (
        ("bank", "Bank", "asset"),
        ("member_savings", "Savings", "liability"),
        ("member_shares", "Shares", "equity"),
        ("dues_income", "Dues", "income"),
    ):
        await db.execute(
            "INSERT INTO accounts(id,cooperative_id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
            uuid.uuid4(),
            cid,
            code,
            name,
            kind,
        )

    payment = await payment_service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=125000,
        provider="manual",
        provider_reference="REF-123",
        received_on=date(2024, 3, 2),
        idempotency_key="bank-stmt-payment-1",
    )
    payload = b"Date,Narration,Reference,Amount\n2024-03-03,Transfer REF-123,REF-123,1250.00\n"
    imported = await service.import_statement_file(
        session(cid, officer, "officer"),
        content=payload,
        filename="accept.csv",
        column_map={
            "date": "Date",
            "narration": "Narration",
            "reference": "Reference",
            "amount": "Amount",
        },
    )
    assert imported["inserted_count"] == 1
    line_id = await db.fetchval(
        "SELECT id FROM bank_statement_lines WHERE cooperative_id=$1 ORDER BY created_at",
        cid,
    )
    assert line_id is not None

    matches = await service.auto_match_statement_lines(
        session(cid, treasurer, "treasurer"),
        window_days=5,
    )
    assert matches["matched_count"] == 1

    with pytest.raises(Conflict, match="different user"):
        await service.accept_statement_line(
            session(cid, officer, "officer"),
            line_id,
            idempotency_key="statement-accept-officer",
        )

    accepted = await service.accept_statement_line(
        session(cid, treasurer, "treasurer"),
        line_id,
        idempotency_key="statement-accept-treasurer",
    )
    assert accepted["status"] == "matched"
    assert accepted["payment_id"] == str(payment["id"])
    payment_row = await db.fetchrow(
        "SELECT status FROM payments WHERE cooperative_id=$1 AND id=$2",
        cid,
        payment["id"],
    )
    assert payment_row["status"] == "confirmed"


async def test_ignore_statement_line_requires_note_and_can_be_undone(db):
    seed = await create_tenant(
        db,
        slug_prefix="bankstmt-ignore",
        roles=("officer", "treasurer"),
    )
    cid = seed.cooperative_id
    actor = seed.user_ids[0]
    payload = b"Date,Narration,Amount\n2024-04-05,Unrelated credit,1000.00\n"
    imported = await service.import_statement_file(
        session(cid, actor, "officer"),
        content=payload,
        filename="ignore.csv",
        column_map={"date": "Date", "narration": "Narration", "amount": "Amount"},
    )
    assert imported["inserted_count"] == 1
    line_id = await db.fetchval(
        "SELECT id FROM bank_statement_lines WHERE cooperative_id=$1 ORDER BY created_at",
        cid,
    )
    assert line_id is not None

    with pytest.raises(ValueError, match="Ignore note is required"):
        await service.ignore_statement_line(
            session(cid, actor, "officer"),
            line_id,
            note="   ",
        )

    ignored = await service.ignore_statement_line(
        session(cid, actor, "officer"),
        line_id,
        note="Bank charge",
    )
    assert ignored["status"] == "ignored"

    undone = await service.undo_statement_decision(session(cid, actor, "officer"), line_id)
    assert undone["status"] == "unmatched"
