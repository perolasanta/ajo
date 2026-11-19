# SQL Ambiguity Fixes and Integration Tests for Loan Product and Policy Approval

This change fixes SQL ambiguity errors in `list_loan_products` and `get_payment` queries by qualifying all columns with table aliases. It adds comprehensive integration tests exercising the approval workflows and verifying the SQL queries work correctly with JOINs to the users table.

**Watch for**: The scope of the SQL audit may be incomplete. The task specification required fixing "every raw SQL that joins users", but the diff only addresses 2 queries. Other code paths (payroll, auth, notifications, disbursements, members audits) that JOIN users were not examined for similar issues, though spot checks suggest they are already properly qualified.

**Verdict**: NEEDS_CHANGES

---

## High-level view

The two SQL queries that were broken (list_loan_products raising "column reference status is ambiguous" and get_payment with unqualified payment columns) are now fixed by explicitly qualifying all columns with `p.` for loan_products and payments, and `m.` for members. The fixes are targeted and correctly identify the ambiguous columns that conflicted when JOINing to the users table (status, created_at, approved_at, etc.).

The integration tests properly exercise the approval workflows end-to-end via the service layer, creating tenants, proposing and approving policies and loan products, and verifying that proposer and approver names are populated correctly from the users table. Tests use seeded cooperatives and handle multi-admin scenarios correctly.

The scope question: the task spec (step 1) asked to "grep backend/app for every raw SQL that joins users... and fix every one." The diff addresses only the queries mentioned in the error symptoms (list_loan_products via policies/service.py and the payments flow), not an exhaustive audit of all such queries. A review of other files listed in the grep output (payroll, audit, auth, notifications, disbursements, members) shows they already use proper aliases, so they don't have the ambiguity problem—but this verification was not done by the coder as part of this PR.

---

<details>
<summary>Issues (1)</summary>

1. **Incomplete SQL audit** — The task specification explicitly required auditing "every raw SQL that joins users" and fixing all ambiguous columns. The diff only fixes 2 queries. While spot checks suggest other code paths are already properly qualified, the systematic audit called for in the spec was not performed or documented.

</details>

---

<details>
<summary>Details</summary>

### SQL qualification fixes

In `list_loan_products`, columns that conflicted with the users table (status, created_at, approved_at, min_amount_kobo, max_amount_kobo, min_tenor_months, max_tenor_months, fee_bps, guarantors_required, supersedes_id) are now prefixed with `p.`. In `get_payment`, payment columns are now qualified with `p.`. Both queries properly qualify the WHERE and ORDER BY clauses as well. The JOIN structure and user table references (aliases proposer, approver, recorder) were already properly qualified.

### Integration tests and role workflows

The tests exercise the broken workflows end-to-end via the service layer. `test_list_loan_products_with_proposer_and_approver` and `test_list_policy_versions_with_proposer_and_approver` verify that the queries execute without ambiguity errors and that proposer/approver names are populated; both include dividend_frequency in results as required.

Tests handle the real-world case of deleted proposers/approvers by verifying the "Unknown user" fallback works. Role-based tests verify a second admin can approve another admin's proposals, matching the staging symptom where the second admin was blocked.

A gap: the self-approval loan product test (`test_proposer_cannot_approve_own_loan_product`) does not assert an exception, but the docstring notes "Currently there is no guard." The policy self-approval test does assert `Conflict`. This asymmetry leaves an unguarded pathway in production—loan products can be approved by their proposers, while policies cannot.

`test_get_routes_accessible_as_admin` is the generic guard requested in the spec: it verifies service functions backing GET routes don't raise on the happy path and documents what it skips (HTTP-level routes, parameterized paths, auth middleware).

### Incomplete SQL audit

The task specification explicitly required auditing "every raw SQL that joins users" and fixing all ambiguous columns. The diff only fixes the 2 queries that were directly causing the staging outage. Spot checks of payroll, audit, auth service, and members reads show they already use explicit table aliases (created_user, posted_user, u), so the ambiguity problem doesn't exist there. However, this verification was not performed or documented by the coder. The spec asked for a systematic "grep backend/app... and fix every one"; at minimum, the PR should list which files were audited and their compliance status.

</details>

---

<details>
<summary>File map</summary>

- `backend/app/domains/policies/service.py`: Qualified loan_products columns with `p.` prefix in `list_loan_products` query.
- `backend/app/domains/payments/service.py`: Qualified payments columns with `p.` prefix in `get_payment` query.
- `backend/tests/integration/test_policies_approval.py`: Added 7 integration tests covering list queries, approval workflows, role separation, and generic route guard.

</details>
