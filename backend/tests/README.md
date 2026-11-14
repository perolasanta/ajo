# Test templates

Use the closest passing test in the same module as the starting point. These examples are copied or narrowly adapted from passing tests in this repository; the source is listed before each template. Keep imports and fixtures aligned with the module being tested.

The shared `notification_settings` fixture is available to tests that need deterministic channel settings. It explicitly sets `EMAIL_ENABLED`, `TELEGRAM_ENABLED`, and `PUBLIC_BASE_URL`; request it as a test argument, then adjust the returned settings in the test if needed.

## API access: allowed role

Source: `integration/test_role_access_endpoints.py::test_screen_read_endpoint_is_available_to_matrix_role_without_csrf`.

```python
@pytest.mark.parametrize("role", ["officer", "admin"])
async def test_endpoint_is_available_to_allowed_role(
    role: str,
    db: asyncpg.Connection,
    role_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(
        db,
        name="Role access API test",
        slug_prefix="role-access",
        roles=("officer", "admin", "treasurer", "auditor"),
        include_member=True,
    )
    client, selected = role_api
    selected["current"] = _session(seed, role)
    assert seed.member_id is not None

    response = await client.get("/api/v1/members")

    assert response.status_code == 200, response.text
    assert any(item["id"] == str(seed.member_id) for item in response.json())
```

## API access: forbidden role

Source: `unit/test_audit_read_contracts.py::test_other_roles_receive_403`.

```python
@pytest.mark.parametrize("role", ["officer", "treasurer", "committee", "member"])
async def test_forbidden_role_receives_403(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(audit_api.router, prefix="/api/v1")
    app.dependency_overrides[audit_api.current_session] = lambda: _session(role)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/audit-events")

    assert response.status_code == 403
```

## API access: tenant isolation

Source: route and response assertions are adapted from `integration/test_role_access_endpoints.py::test_screen_read_endpoint_is_available_to_matrix_role_without_csrf`; the two-tenant isolation setup follows the passing pattern in `integration/test_payments_inbox.py::test_payment_reads_are_tenant_isolated`.

```python
async def test_member_list_contains_only_the_current_tenant(
    db: asyncpg.Connection,
    role_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    tenant_a = await create_tenant(
        db,
        slug_prefix="tenant-a",
        roles=("officer", "admin", "treasurer", "auditor"),
        include_member=True,
    )
    tenant_b = await create_tenant(
        db,
        slug_prefix="tenant-b",
        roles=("officer", "admin", "treasurer", "auditor"),
        include_member=True,
    )
    assert tenant_a.member_id is not None
    assert tenant_b.member_id is not None
    client, selected = role_api
    selected["current"] = _session(tenant_a, "officer")

    response = await client.get("/api/v1/members")

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()} == {str(tenant_a.member_id)}
    assert str(tenant_b.member_id) not in response.text
```

## Service test with a database

Source: `integration/test_payments_inbox.py::test_member_balance_matches_cash_contribution_ledger`.

```python
async def test_member_balance_matches_cash_contribution_ledger(
    db: asyncpg.Connection, notification_settings: Settings
) -> None:
    cid, officer, _, member_id, _ = await payment_tenant(db)
    await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="savings",
        amount_kobo=9876,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-balance-1",
    )
    assert (await member_balances(cid, member_id))["savings_kobo"] == 9876
```

This test requests `notification_settings` even though the channels are disabled, so behavior does not inherit a developer's local channel settings.

## Alert test: one row per eligible recipient, not the actor

Source: `integration/test_payroll.py::test_treasurer_post_queues_signoff_for_every_admin_and_updates_badges`.

```python
notification_settings.email_enabled = True
assert len(rows) == 2
assert {
    (row["recipient_user_id"], row["kind"], row["entity_id"], row["channel"], row["status"])
    for row in rows
} == {
    (first_admin_id, "payroll_sign_off", run_id, "email", "pending"),
    (second_admin_id, "payroll_sign_off", run_id, "email", "pending"),
}
assert treasurer_id not in {row["recipient_user_id"] for row in rows}
```

## CHECK constraint test

Source: `integration/test_payments_inbox.py::test_notifications_constraint_matches_action_specs`.

```python
async def test_constraint_matches_action_specs(db: asyncpg.Connection) -> None:
    definition = await db.fetchval(
        "SELECT pg_get_constraintdef(oid, true) FROM pg_constraint "
        "WHERE conname = 'notifications_action_fields_check'"
    )
    assert definition is not None
    kinds = check_constraint_array_values(definition, column="kind")
    assert kinds == set(ACTION_SPECS)
```

Use `tests.helpers.postgres.check_constraint_array_values` rather than asserting on PostgreSQL's rendered formatting. Its parser handles the `= ANY (ARRAY[...])` form.

## Frontend Node test with a stable message assertion

Source: `../../frontend/src/payrollPolicy.test.mjs`.

```javascript
test("payroll file validation accepts just under 5 MiB and rejects just over or non-CSV files", () => {
  const valid = { name: "payroll.csv", type: "text/csv", size: maxPayrollUploadBytes - 1 };
  assert.equal(validatePayrollFile(valid), null);
  assert.equal(
    validatePayrollFile({ ...valid, size: maxPayrollUploadBytes + 1 }),
    "Payroll files must be 5 MiB (5 × 1024 × 1024 bytes) or smaller.",
  );
  assert.match(validatePayrollFile({ ...valid, name: "payroll.xlsx" }), /\.csv/);
  assert.match(validatePayrollFile({ ...valid, type: "application/vnd.ms-excel" }), /text\/csv/);
});
```
