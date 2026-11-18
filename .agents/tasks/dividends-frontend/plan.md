# Implementation Plan — Dividends frontend (unit U6)

Investigation-only plan. Backend (`backend/app/domains/dividends/`) is complete and CI-green;
only the frontend is missing. All facts below are read from the repo.

Workspace root: `/home/petbell/Documents/Github/ajo`. Frontend root: `frontend/`.
Verification runs from `frontend/`.

---

## ⚠️ BLOCKING CONFLICTS — owner must decide before implementation

While reading the CI-green backend against this task's spec, I found **two hard divergences**
between the task description and the backend. The AGENTS.md rule "if the code and the spec
disagree, stop and tell me" applies. A correct, non-broken implementation is not possible until
these are resolved. Recommended defaults are given so the implementer can proceed once confirmed.

### Conflict A — Dividends read/access roles (task spec vs backend enforcement)

Backend enforced roles (`backend/app/domains/dividends/service.py`, `_role(...)`):

| Endpoint | Backend-enforced roles |
|---|---|
| `POST /dividends/runs` (prepare) | treasurer, admin |
| `GET /dividends/runs` (list) | treasurer, admin, **officer** |
| `GET /dividends/runs/{run_id}` (detail) | treasurer, admin, **officer** |
| `POST .../approve` | admin |
| `POST .../reject` | admin |
| `POST .../cancel` | treasurer, admin (+ preparer-or-admin check) |
| `POST .../post` | admin, treasurer |

Notification badge (`backend/app/domains/notifications/reads.py`): `summary.dividends.awaiting_approval`
is populated **only when `"admin" in roles`**. Notification recipients (`eligibility.py`
`DIVIDEND_APPROVAL`) = `{"admin"}`, excluding the preparer (`excluded_user_ids = [r.prepared_by]`).

Task spec says: **officer has NO menu entry and NO data; auditor and committee are read-only
WITHOUT action buttons; treasurer prepares and cancels; admin approves and rejects.**

These are incompatible: the backend lets **officer** read and **blocks auditor/committee**; the
task wants the **opposite** (officer excluded, auditor/committee read-only). If the frontend nav
included auditor/committee they would get HTTP 403 (broken UX); the frontend cannot grant access
the backend denies.

**DECISION (confirmed by owner — NO backend change in this unit):** Dividends nav entry roles =
`["treasurer", "admin"]` only. Officer is excluded entirely (matches the spec). Auditor and
committee are ALSO excluded from nav and read, because the backend `list`/`get` role set is
`{treasurer, admin, officer}` and denies auditor/committee — including them would 403. Action
buttons stay exactly per the spec: treasurer prepares + cancels, admin approves + rejects, never
show Approve to the preparer, auditor/committee get no action buttons.

**PR note to add to the PR description:** "Backend currently grants officer read and denies
auditor/committee, opposite to the documented rule 'officer none; auditor and committee read-only'.
Frontend excludes auditor/committee to avoid 403s. Recommend a backend follow-up to change read
roles to `{treasurer, admin, auditor, committee}` (drop officer)."

### Conflict B — Prepare form requires `declared_by_meeting_id`, but there is NO meetings API

Backend `PrepareDividendRunRequest` (`router.py` lines 21–28) **requires** `declared_by_meeting_id`
(a meeting UUID) plus `record_date: date` and `pool_kobo: int`. There is **no meetings domain,
router, or table** anywhere in the backend (confirmed: `grep` for `meetings` finds nothing) and no
meetings reference in `frontend/src`. So the frontend cannot populate a meeting selector.

The task's prepare form fields are **"financial year, record date, pool in naira"** — there is no
`financial_year` field in the API, and `declared_by_meeting_id` (required) is not listed. The same
"year" concept also appears as a **runs-list column** ("status, year, pool in naira, record date")
and again has no backing API field.

**DECISION (confirmed by owner — NO backend change in this unit):** prepare form fields =
`record_date` (date input), `pool` in naira (number → converted to `pool_kobo = round(naira*100)`),
and a **required "Declared by meeting ID"** UUID text input that maps to `declared_by_meeting_id`
(the API requires it; it is a free UUID with no meetings table/FK behind it, so a text input is the
only API-compatible option without a backend change). "Financial year" is dropped — there is no such
API field. The list "year" column is derived from `record_date` (`record_date.slice(0, 4)`). The TS
`DividendRunResponse` type still includes `declared_by_meeting_id` (it is a response field).

**PR note to add to the PR description:** "API requires `declared_by_meeting_id` and has no
`financial_year` field; the form uses a meeting-ID input and the list year is derived from
`record_date`. Recommend a backend follow-up if a meetings picker or a true `financial_year` field
is wanted."

> Items 3, 5, 6, and 8 below are drafted against these two confirmed decisions.

---

## 1. Backend mirror (exact facts to reproduce)

Routes (prefix `/api/v1` is added by `app.main`; client uses `API_BASE = "/api/v1"`):

| Method | Path | Body | Returns | Role (backend) |
|---|---|---|---|---|
| POST | `/dividends/runs` | `PrepareDividendRunRequest` | `DividendRunResponse` | treasurer, admin |
| GET | `/dividends/runs?page=&page_size=` | — | `DividendRunListResponse` | treasurer, admin, officer |
| GET | `/dividends/runs/{run_id}` | — | `DividendRunResponse` (+ `items`) | treasurer, admin, officer |
| POST | `/dividends/runs/{run_id}/approve` | — | `DividendRunResponse` | admin |
| POST | `/dividends/runs/{run_id}/reject` | `RejectDividendRunRequest` | `DividendRunResponse` | admin |
| POST | `/dividends/runs/{run_id}/cancel` | — | `DividendRunResponse` | treasurer, admin |
| POST | `/dividends/runs/{run_id}/post` | — | `DividendRunResponse` | admin, treasurer |

Schemas (`router.py`, exact field names/types):

- `PrepareDividendRunRequest`: `record_date: date`, `declared_by_meeting_id: str`, `pool_kobo: int` (gt 0).
- `RejectDividendRunRequest`: `reason: str`.
- `DividendLineResponse`: `line_id: str`, `member_id: str`, `shares_held: int`, `amount_kobo: int`, `created_at: str`.
- `DividendRunResponse`: `run_id: str`, `record_date: str`, `declared_by_meeting_id: str`, `pool_kobo: int`, `prepared_by: str`, `prepared_at: str`, `approved_by: str|None`, `approved_at: str|None`, `rejected_by: str|None`, `rejected_at: str|None`, `rejection_reason: str|None`, `posted_by: str|None`, `posted_at: str|None`, `status: str`, `created_at: str`, `updated_at: str`, `items: list[DividendLineResponse]|None` (None in list responses; populated only by detail GET).
- `DividendRunListResponse`: `items: list[DividendRunResponse]`, `total: int`, `page: int`, `page_size: int`.

Status enum values (from `service.py`): `unprocessed` → `approved` | `rejected` | `cancelled` (only from `unprocessed`); `approved` → `posted`. Created runs start `unprocessed`.

Money fields (integer kobo): `pool_kobo`, `amount_kobo`. `shares_held` is integer kobo of the `member_shares` account. Date fields: `record_date` (ISO date string), timestamps `*_at` (ISO datetime strings). Actor-id fields: `prepared_by`, `approved_by`, `rejected_by`, `posted_by` (UUID strings, nullable except `prepared_by`).

Notification deep-link target for dividend approvals (`eligibility.py`): `/dividends/{id}` (per-run).

---

## 2. TS types in `frontend/src/api/schema.d.ts`

Add these types near the other response types (e.g. after `NotificationSummary`/`StaffRecord`).
Field names and nullability mirror `router.py` exactly. **Do not modify `NotificationSummary`** —
it already contains `dividends?: { awaiting_approval?: number };` (the `dividend_approval` kind is
already present). Do **not drop any existing kind** (dropping one is the ResponseValidationError-class
bug). Only ADD the types below.

```ts
export type DividendLineResponse = {
  line_id: string;
  member_id: string;
  shares_held: number;
  amount_kobo: number;
  created_at: string;
};

export type DividendRunResponse = {
  run_id: string;
  record_date: string;
  declared_by_meeting_id: string;
  pool_kobo: number;
  prepared_by: string;
  prepared_at: string;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  posted_by: string | null;
  posted_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  items: DividendLineResponse[] | null;
};

export type DividendRunListResponse = {
  items: DividendRunResponse[];
  total: number;
  page: number;
  page_size: number;
};

export type PrepareDividendRunRequest = {
  record_date: string;
  declared_by_meeting_id: string;
  pool_kobo: number;
};

export type RejectDividendRunRequest = { reason: string };
```

Files: `frontend/src/api/schema.d.ts`
Verify: `cd frontend && npm run typecheck` — passes with the new types referenced from `client.ts`.

---

## 3. Client functions in `frontend/src/api/client.ts`

Match the existing `request<T>(path, init?)` style (same as `getSharePurchases`,
`requestSharePurchase`, `rejectPolicy`). Add the new type names to the existing `import type { ... }`
block from `"./schema"`. Use `encodeURIComponent` for path params (same as `getMember`,
`getMemberStatement`). No `Idempotency-Key` (the dividend endpoints take none; matches
`requestSharePurchase` which has none). **Do NOT add `postDividendRun`** — the UI spec lists
prepare/approve/reject/cancel only; no existing pattern expects `post`, so omit unused code (YAGNI).

```ts
export function getDividendRuns(
  params: { page?: number; pageSize?: number } = {},
): Promise<DividendRunListResponse> {
  const query = new URLSearchParams();
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 25));
  return request<DividendRunListResponse>(`/dividends/runs?${query.toString()}`);
}

export function getDividendRun(runId: string): Promise<DividendRunResponse> {
  return request<DividendRunResponse>(`/dividends/runs/${encodeURIComponent(runId)}`);
}

export function prepareDividendRun(body: PrepareDividendRunRequest): Promise<DividendRunResponse> {
  return request<DividendRunResponse>("/dividends/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function approveDividendRun(runId: string): Promise<DividendRunResponse> {
  return request<DividendRunResponse>(
    `/dividends/runs/${encodeURIComponent(runId)}/approve`,
    { method: "POST" },
  );
}

export function rejectDividendRun(runId: string, reason: string): Promise<DividendRunResponse> {
  return request<DividendRunResponse>(
    `/dividends/runs/${encodeURIComponent(runId)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
}

export function cancelDividendRun(runId: string): Promise<DividendRunResponse> {
  return request<DividendRunResponse>(
    `/dividends/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
}
```

Files: `frontend/src/api/client.ts`
Verify: `cd frontend && npm run typecheck` — compiles; the new functions are importable.

---

## 4. Pure-logic module `frontend/src/dividendsPolicy.mjs` (+ `dividendsPolicy.d.mts`)

Create a pure JS module (mirrors `sharePurchasePolicy.mjs` / `disbursementPolicy.mjs`) holding the
testable role-gating and member-label logic, plus request-body builders. Because `DividendsPage.tsx`
imports it from TypeScript, add a companion `dividendsPolicy.d.mts` (same pattern as
`sharePurchasePolicy.d.mts`).

Logic (defaults per Conflict A/B):

```js
// roles: string[], status: string, preparedBy: string | null | undefined, currentUserId: string
export function canPrepareDividendRun(roles) {
  return Array.isArray(roles) && roles.includes("treasurer");
}
export function canApproveDividendRun(roles, status, preparedBy, currentUserId) {
  return (
    Array.isArray(roles) &&
    roles.includes("admin") &&
    status === "unprocessed" &&
    preparedBy !== currentUserId
  );
}
export function canRejectDividendRun(roles, status, preparedBy, currentUserId) {
  return canApproveDividendRun(roles, status, preparedBy, currentUserId);
}
export function canCancelDividendRun(roles, status, preparedBy, currentUserId) {
  return status === "unprocessed" && preparedBy === currentUserId;
}

// member: { full_name?: string | null } | null | undefined
export function memberDisplayLabel(member) {
  if (!member) return "Unknown user";
  const name = (member.full_name ?? "").trim();
  return name ? name : "Unnamed user";
}

// build only API-allowed fields; never member names or per-member amounts
export function prepareDividendRequestBody({ recordDate, declaredByMeetingId, poolNaira }) {
  return {
    record_date: recordDate,
    declared_by_meeting_id: declaredByMeetingId,
    pool_kobo: Math.round((Number(poolNaira) || 0) * 100),
  };
}
export function rejectDividendRequestBody(reason) {
  return { reason: String(reason ?? "") };
}
```

`dividendsPolicy.d.mts`:

```ts
export function canPrepareDividendRun(roles: string[]): boolean;
export function canApproveDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function canRejectDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function canCancelDividendRun(
  roles: string[],
  status: string,
  preparedBy: string | null | undefined,
  currentUserId: string,
): boolean;
export function memberDisplayLabel(
  member: { full_name?: string | null } | null | undefined,
): string;
export function prepareDividendRequestBody(args: {
  recordDate: string;
  declaredByMeetingId: string;
  poolNaira: number;
}): { record_date: string; declared_by_meeting_id: string; pool_kobo: number };
export function rejectDividendRequestBody(reason: string): { reason: string };
```

Role-gating summary (the buttons the page renders):

| Button | Shown when |
|---|---|
| Prepare form | `canPrepareDividendRun(roles)` — treasurer only |
| Approve | `canApproveDividendRun(...)` — admin AND `status==="unprocessed"` AND `prepared_by !== currentUserId` |
| Reject (note required) | `canRejectDividendRun(...)` — same as Approve; prompts for a reason |
| Cancel | `canCancelDividendRun(...)` — the preparer (`prepared_by === currentUserId`) AND `status==="unprocessed"` |
| (auditor, committee, officer) | no action buttons; officer has no menu entry |

Files: `frontend/src/dividendsPolicy.mjs`, `frontend/src/dividendsPolicy.d.mts`
Verify: `cd frontend && node --test src/dividendsPolicy.test.mjs` (see item 9) passes; `npm run typecheck` passes (the `.d.mts` resolves the import in `DividendsPage.tsx`).

---

## 5. Navigation changes `frontend/src/navigation.js` (desktop + mobile share one menu)

The menu is assembled once in `App.tsx` `Shell` from `visibleNavigation` + `menuActionCount` +
`navigationTarget` + `formatMenuBadge`, so a single nav entry + `menuActionCount`/`navigationTarget`
cases drive BOTH desktop sidebar and the mobile hamburger drawer automatically.

- Add a nav entry to the `navigation` array (placement: after "Share register", before "Loans" to
  group share/finance pages). Roles per **Conflict A recommended default**:
  ```js
  { name: "Dividends", path: "/dividends", roles: ["treasurer", "admin"] },
  ```
- Add a `routePatterns` entry so `getRouteDecision` allows `/dividends` and `/dividends/:runId`:
  ```js
  ["/dividends", /^\/dividends(?:\/[^/]+)?$/],
  ```
- Add a `menuActionCount` case (admin-only badge; matches `reads.py` which gates the dividend count
  under `if "admin" in roles`):
  ```js
  if (name === "Dividends" && roles.includes("admin")) {
    count += summary?.dividends?.awaiting_approval ?? 0;
  }
  ```
- Add a `navigationTarget` case (deep-link to a "needs my approval" view; the page interprets
  `?action=needed`):
  ```js
  if (name === "Dividends") return count > 0 ? "/dividends?action=needed" : "/dividends";
  ```

`navigation.d.ts` needs **no change** — the existing `navigation: NavigationItem[]`,
`menuActionCount(...)`, and `navigationTarget(...)` declarations already cover the new entry/cases.

Files: `frontend/src/navigation.js` (edit). `frontend/src/navigation.d.ts` (no change — confirm only).
Verify: `cd frontend && node --test src/navigation.test.mjs` (after item 9 adds Dividends cases) passes; `npm run typecheck` passes.

---

## 6. Dividends page `frontend/src/pages/DividendsPage.tsx`

One component handles **both** `/dividends` (list + prepare form) and `/dividends/:runId` (run
detail) via `useParams<{ runId?: string }>()`. This honors the task's single-file naming and makes
the notification deep-link `/dividends/{id}` resolve (the eligibility target is `/dividends/{id}`).
Pattern to follow: the session/roles fetch from `SharePurchasesPage.tsx` and the list+detail shape
from `Loans`/`Disbursements`/`Payroll`.

Shared setup (both views):
- `const session = useQuery({ queryKey: ["session"], queryFn: () => request<Session>("/auth/me") })`
  with `type Session = { user_id: string; roles: string[] }`.
- `const roles = session.data?.roles ?? []`; `const userId = session.data?.user_id`.
- `<PageHeader title="Dividends" breadcrumb="Members / Dividends" />` (matches the share pages'
  grouping). Use `Card`, `Button`, `Badge`, `EmptyState`, `Skeleton`, `Money`, `DateTime` from
  `../ui/*` (props: `<Money kobo={number} />`, `<DateTime value={string} />`,
  `<PageHeader title breadcrumb />`).
- Read `?action=needed` from `useLocation().search`; when present, the list filters client-side to
  `status === "unprocessed"` (the API has no status filter param).

**List view** (`runId` absent):
- `getDividendRuns({ page, pageSize: 25 })` via `useQuery({ queryKey: ["dividend-runs", page, action] })`.
- Table columns (exact, per task): **Status** (`<Badge>{status.replaceAll("_"," ")}</Badge>`),
  **Year** (derived `run.record_date.slice(0, 4)` — see Conflict B note), **Pool** (`<Money kobo={run.pool_kobo} />`),
  **Record date** (`run.record_date`). Each row links to `/dividends/{run_id}`.
- Pagination footer (Previous/Next) like `ShareRegisterPage` using `total`/`page_size`.
- Empty/loading/error states: `Skeleton` while pending; `EmptyState` ("No dividend runs") when
  empty; `EmptyState` with the `ApiError` message on error (mirror `BankStatementsPage.errorMessage`).

**Prepare form** (shown only when `canPrepareDividendRun(roles)` — treasurer), per Conflict B
recommended default:
- Fields: **Record date** (`<input type="date">`), **Pool in naira** (`<input type="number"
  step="0.01" min="0">`), **Declared by meeting ID** (required text input for the meeting UUID —
  maps to `declared_by_meeting_id`). (Drop "financial year"; no API field.)
- On submit: build body via `prepareDividendRequestBody({ recordDate, declaredByMeetingId,
  poolNaira })` and call `prepareDividendRun(body)` in a `useMutation`; on success invalidate
  `["dividend-runs"]` and `["notification-summary"]`, then navigate to `/dividends/{run_id}`.
- Disable submit until record date, meeting ID, and a positive pool are present; show
  `create.isError` message via `EmptyState`/alert text (no member names in the form).

**Run detail view** (`runId` present):
- `getDividendRun(runId)` via `useQuery({ queryKey: ["dividend-run", runId] })`.
- Build a member map once: `getMembers()` → `Map<id, { full_name: string }>` (`getMembers()` returns
  all members, same call `SharePurchasesPage` uses for its member dropdown). Resolve each line's
  `member_id` via the map; render the label through `memberDisplayLabel(map.get(line.member_id))`
  → NAME ONLY, `Unnamed user` for an existing member with empty name, `Unknown user` for a missing
  record.
- Run header: status `<Badge>`, record date, pool `<Money>`, prepared actor (resolved through
  `memberDisplayLabel` against the map — note: `prepared_by` is a user id, looked up the same way).
- Lines table columns: **Member** (label), **Shares held** (`Intl.NumberFormat("en-NG").format(shares_held)`),
  **Dividend** (`<Money kobo={line.amount_kobo} />`). Totals row: total shares (sum), total dividend
  (sum of `amount_kobo`), and **per-share rate** = `pool_kobo / totalShares` displayed as
  `` `${(rate * 100).toFixed(1)}% of share value` `` when `totalShares > 0`.
- Action buttons (only when `status === "unprocessed"`):
  - **Approve** — shown iff `canApproveDividendRun(roles, run.status, run.prepared_by, userId)`; calls
    `approveDividendRun(runId)`; confirm dialog; on success invalidate and refetch.
  - **Reject** — shown iff `canRejectDividendRun(...)`; prompts for a reason (required — block if
    empty, mirroring `SharePurchasesPage.decide`'s reject-note guard); calls
    `rejectDividendRun(runId, reason)`; on success invalidate.
  - **Cancel** — shown iff `canCancelDividendRun(roles, run.status, run.prepared_by, userId)`; calls
    `cancelDividendRun(runId)`; on success invalidate.
  - When the current user prepared the run and is also admin, Approve/Reject are hidden (the maker
    never approves their own run); show an informational note ("You prepared this run; another admin
    must approve it") mirroring `SharePurchasesPage`'s "You requested this; another treasurer must
    decide" pattern.
  - Auditor/committee/officer (if they ever reach the detail) see the table read-only with no
    buttons (the gating functions return false for them).
- Back link to `/dividends`.

Files: `frontend/src/pages/DividendsPage.tsx`
Verify: `cd frontend && npm run typecheck` and `npm run build` pass; `node --test src/dividendsPolicy.test.mjs` and `src/navigation.test.mjs` pass.

---

## 7. App.tsx route wiring `frontend/src/App.tsx`

- Add the lazy import with the other page lazy imports:
  `const DividendsPage = lazy(() => import("./pages/DividendsPage"));`
- Inside `<Route element={<ProtectedShell />}>`, add (after the share routes, before `loans`):
  ```tsx
  <Route path="/dividends" element={<DividendsPage />} />
  <Route path="/dividends/:runId" element={<DividendsPage />} />
  ```
  (Both routes use the same component; `useParams` decides list vs detail. The route guard is
  `getRouteDecision` via `ProtectedShell`, fed by the new `routePatterns` entry from item 5.)

Files: `frontend/src/App.tsx`
Verify: `cd frontend && npm run typecheck` and `npm run build` pass; navigating `/dividends` and a
`/dividends/<id>` deep-link render the page (manual, or via the design shells).

---

## 8. Tests `frontend/src/dividendsPolicy.test.mjs` + extend `frontend/src/navigation.test.mjs`

### 8a. New `frontend/src/dividendsPolicy.test.mjs` (pure-logic; `node --test`)

Mirror `sharePurchasePolicy.test.mjs` (import from `./dividendsPolicy.mjs`, use
`assert`/`test` from `node`). Cover:

- Role gating: `canPrepareDividendRun` true only for treasurer (admin/officer/auditor/committee
  false). `canApproveDividendRun`/`canRejectDividendRun`: admin true only when `status==="unprocessed"`
  and `preparedBy !== currentUserId`; false for the preparer-admin (the maker-checker rule),
  non-admins, and non-unprocessed statuses. `canCancelDividendRun`: true only for the preparer on an
  unprocessed run; false for a non-preparer and for non-unprocessed status.
- Member-label fallbacks: `memberDisplayLabel(undefined)` → `"Unknown user"`;
  `memberDisplayLabel(null)` → `"Unknown user"`; `memberDisplayLabel({ full_name: "" })` →
  `"Unnamed user"`; `memberDisplayLabel({ full_name: "   " })` → `"Unnamed user"`;
  `memberDisplayLabel({ full_name: "Ada Okafor" })` → `"Ada Okafor"`;
  `memberDisplayLabel({ full_name: null })` → `"Unnamed user"`. Use `assert.equal` for these exact
  string comparisons (allowed — these are value strings, not regex).
- No names/amounts in forbidden payloads: `prepareDividendRequestBody({ recordDate: "2025-06-30",
  declaredByMeetingId: "m-uuid", poolNaira: 1000 })` returns exactly
  `{ record_date, declared_by_meeting_id, pool_kobo }` with `pool_kobo === 100000` and **no**
  `member_name`/`member_id`/per-member keys (`assert.deepEqual` against the exact object; then
  `assert.ok(!("member_name" in body))`). `rejectDividendRequestBody("bad")` returns exactly
  `{ reason: "bad" }` (`assert.deepEqual`) — no names, no amounts. Use `assert.match`/`re.search`
  style only for regex checks; never compare a string to a regex with `==`/`strictEqual`.

### 8b. Extend `frontend/src/navigation.test.mjs` (already in `test:roles`)

Add tests mirroring the existing "Share purchases"/"Bank statements" cases:
- Dividends nav entry exists with `roles: ["treasurer", "admin"]` (Conflict A default); for each of
  treasurer/admin/officer/auditor/committee/member assert whether they see Dividends in
  `visibleNavigation` and whether `getRouteDecision([role], "/dividends")` and
  `getRouteDecision([role], "/dividends/<id>")` are allowed (treasurer/admin true; others false).
- Badge: `menuActionCount("Dividends", { dividends: { awaiting_approval: 3 } }, ["admin"]) === 3`;
  for officer/treasurer/auditor/committee it is `0` (admin-only). Use `assert.equal`.
- Deep-link: `navigationTarget("Dividends", 2)` matches `/^\/dividends\?action=needed$/`;
  `navigationTarget("Dividends", 0) === "/dividends"`. Use `assert.match` for the regex one,
  `assert.equal` for the literal.

Files: `frontend/src/dividendsPolicy.test.mjs` (new); `frontend/src/navigation.test.mjs` (edit).
Verify: `cd frontend && node --test src/dividendsPolicy.test.mjs` and
`node --test src/navigation.test.mjs` pass.

---

## 9. Add the new test to `frontend/package.json` `test:roles`

Add `src/dividendsPolicy.test.mjs` to the `test:roles` script's `node --test` file list (after
`src/shareRegisterPage.test.mjs`). `src/navigation.test.mjs` is already listed, so no addition
needed for 8b.

Files: `frontend/package.json`
Verify: `cd frontend && npm run test:roles` runs `dividendsPolicy.test.mjs` and all listed tests pass.

---

## Verification (run from `frontend/`)

1. `cd frontend && npm run typecheck` — `tsc --noEmit` passes (new types, `.d.mts`, page, client).
2. `cd frontend && npm run build` — `tsc --noEmit && vite build` passes.
3. `cd frontend && node --test src/dividendsPolicy.test.mjs` — new pure-logic tests pass.
4. `cd frontend && node --test src/navigation.test.mjs` — extended nav tests pass.
5. `cd frontend && npm run test:roles` — full roles suite (including the new file) passes.
6. `cd frontend && npm run format` — Prettier reformats the new/edited files; re-run typecheck after.

Note: per the run-mode rules the owner runs the suite; the implementer runs only the targeted tests
above plus `ruff`/`mypy` are N/A (frontend only). If any check cannot run, say so first in the report.

---

## Files touched (summary)

- NEW `frontend/src/dividendsPolicy.mjs`
- NEW `frontend/src/dividendsPolicy.d.mts`
- NEW `frontend/src/dividendsPolicy.test.mjs`
- NEW `frontend/src/pages/DividendsPage.tsx`
- EDIT `frontend/src/api/schema.d.ts` (add 5 types; do NOT touch `NotificationSummary`)
- EDIT `frontend/src/api/client.ts` (add imports + 6 functions; no `postDividendRun`)
- EDIT `frontend/src/navigation.js` (nav entry, routePattern, menuActionCount, navigationTarget)
- EDIT `frontend/src/navigation.test.mjs` (add Dividends cases)
- EDIT `frontend/src/App.tsx` (lazy import + 2 routes)
- EDIT `frontend/package.json` (add `dividendsPolicy.test.mjs` to `test:roles`)
- NO CHANGE `frontend/src/navigation.d.ts` (existing declarations cover the new behavior)
