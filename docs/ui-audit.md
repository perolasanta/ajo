# UI Audit: Ajo Cooperative Management App

**Date:** 16 Nov 2026  
**Branch:** docs/ui-audit  
**Auditor:** Kiro (using impeccable, design-taste-frontend, refactor-ui skills)

---

## Skills Used

- **impeccable**: Available (v4.7.1), provides design direction framework, routing, craft-floor principles.
- **design-taste-frontend**: Available, enforces professional quality per the "anti-slop" frontend skill.
- **refactor-ui**: Available, provides atomic design heuristics and audit methodology.

---

## 1. Inventory: Frontend Stack & Architecture

### Tech Stack
- **Framework:** React 18 + React Router DOM
- **Styling:** Tailwind CSS v4 with `@theme` CSS variables (self-hosted tokens)
- **Components:** Radix UI primitives (Dialog, Dropdown, Popover, Tabs, Tooltip)
- **State:** TanStack React Query
- **API:** openapi-fetch + openapi-typescript (`.mjs` + `.d.mts` typing convention)
- **Icons:** lucide-react (16/20px, 1.75 stroke)
- **Fonts:** @fontsource-variable Inter + Source Serif 4 (self-hosted, subset latin)
- **Build:** Vite + TypeScript + Prettier

### Routes (20 pages, from `src/App.tsx`)
| Path | Page | Auth |
|------|------|------|
| `/dashboard` | DashboardPage | Protected |
| `/members` | MembersPage | Protected |
| `/members/:memberId` | MemberDetailPage | Protected |
| `/payments` | PaymentsPage | Protected |
| `/payments/:paymentId` | PaymentReceiptPage | Protected |
| `/payments/bank-statements` | BankStatementsPage | Protected |
| `/withdrawal-requests` | WithdrawalRequestsPage | Protected |
| `/share-purchases` | SharePurchasesPage | Protected |
| `/share-register` | ShareRegisterPage | Protected |
| `/loans` | LoansPage | Protected |
| `/loans/new` | NewLoanApplicationPage | Protected |
| `/loans/:applicationId` | LoanDetailPage | Protected |
| `/disbursements` | DisbursementsPage | Protected |
| `/disbursements/:disbursementId` | DisbursementDetailPage | Protected |
| `/payroll` | PayrollPage | PayrollGate (officer/treasurer/admin) |
| `/payroll/runs/:runId` | PayrollDetailPage | PayrollGate |
| `/audit` | AuditPage | admin/auditor |
| `/settings` | CooperativeSettingsPage | admin |
| `/staff` | StaffPage | admin |
| `/account` | AccountPage | Protected |
| `/login` | LoginPage | Public |
| `/design*` | DesignPage + ResponsiveReview | Dev only |

### Shared UI Components (`src/ui/`)
- Button.tsx, Badge.tsx, Card.tsx, Field.tsx, Input.tsx, Select.tsx
- Table.tsx, Avatar.tsx, Overlay.tsx, Feedback.tsx, Timeline.tsx, Format.tsx, PageHeader.tsx

### Styling Approach
- **Tailwind v4:** Uses `@theme` directive in `index.css` to define CSS custom properties
- **Tokens:** Matches DESIGN.md exactly (colors, spacing, radii, shadows)
- **No CSS-in-JS:** All styling via Tailwind utility classes + the theme variables
- **Mobile-first:** 44px min touch targets, responsive breakpoints

---

## 2. Design System as Found

### Colours (from `src/index.css` @theme block)
| Token | Value | Usage |
|-------|-------|-------|
| `--color-bg` | #f2f2ec | Page background |
| `--color-surface` | #ffffff | Cards, panels |
| `--color-border` | #e7e5e0 | Borders |
| `--color-ink` | #1c1917 | Primary text |
| `--color-muted` | #6b665e | Secondary text |
| `--color-primary` | #0e2f2b | Sidebar, CTAs |
| `--color-primary-hover` | #17433d | Hover state |
| `--color-primary-soft` | #e5ece9 | Soft backgrounds |
| `--color-gold` | #e0b04f | Badge pills (dark text only) |
| `--color-warning` | #b45309 | Warning text |
| `--color-warning-soft` | #fef3c7 | Warning backgrounds |
| `--color-danger` | #b42318 | Error text |
| `--color-danger-soft` | #fee4e2 | Error backgrounds |
| `--color-success` | #166534 | Success text |
| `--color-success-soft` | #dcfce7 | Success backgrounds |
| `--color-info` | #1d4ed8 | Info text |
| `--color-info-soft` | #dbeafe | Info backgrounds |

**Finding:** Matches DESIGN.md exactly — consistent implementation.

### Typography
- **Sans:** Inter Variable, tabular numerals on
- **Serif:** Source Serif 4 Variable, 600 weight for page titles
- **Scale:** 12, 13, 14, 15, 16, 20, 24, 30px (defined in `--text-*` tokens)
- **Base:** 15px body, 14px on desktop inputs, 16px on mobile (prevents iOS zoom)

### Spacing & Layout
- **Page padding:** 24px (from DESIGN.md, needs verification in pages)
- **Grid:** 8px base unit
- **Card radius:** 12px
- **Control radius:** 8px
- **Sidebar:** 240px desktop, drawer below 1024px
- **Content max-width:** 1200px

### Inconsistencies Found

| Issue | Location | Evidence |
|-------|----------|----------|
| Multiple button styles | `src/ui/Button.tsx` vs inline in pages | Button.tsx has variant props, but pages also have hardcoded classes |
| Spacing not aligned to 8px grid | Various pages | Need audit to confirm all margins/paddings are multiples of 4/8 |
| Mixed icon sizes | `lucide-react` usage | Some 16px, some 20px, not consistent per context |
| Table responsive behavior | `src/ui/Table.tsx` | Has ResponsiveTable, but not all pages use it |
| Badge variations | `src/ui/Badge.tsx` | Has status badges, but some pages define inline status styles |
| Missing empty states | Various list pages | Need to verify each has proper empty state UI |
| Date formatting | `src/ui/Format.tsx` | Has Format.Date, but usage inconsistent across pages |

---

## 3. State Coverage Matrix

### Per-Page State Handling (to verify)

| Page | Loading | Empty | Error | Success | Money Format | Date Format | Name Display |
|------|---------|-------|-------|---------|--------------|-------------|--------------|
| Dashboard | Skeleton? | Message? | Toast? | Badge count | ₦X,XXX.XX | "24 Oct 2026, 14:05 WAT" | Full name |
| Members | Spinner | "No members" | Error banner | Table | ₦X,XXX.XX | DD MMM YYYY | Per role rules |
| Payments | Spinner | "No payments" | Toast | Badge | ₦X,XXX.XX | WAT | Full name |
| Loans | Spinner | "No loans" | Error | Badge | ₦X,XXX.XX | WAT | Full name |
| Payroll | Spinner | "No runs" | Error | Badge | ₦X,XXX.XX | WAT | Actor label |
| Disbursements | Spinner | "None" | Error | Badge | ₦X,XXX.XX | WAT | Full name |
| Settings | Spinner | N/A | Inline | Toast | N/A | WAT | Actor label |
| Staff | Spinner | "No staff" | Error | Toast | N/A | WAT | "Full Name (email)" |
| Audit | Spinner | "No events" | Error | N/A | N/A | WAT | "Full Name (email)" |

### Findings

**Money Display:** DESIGN.md specifies `₦` with 2 decimals and thousands separators. Need to verify `Format.tsx` handles this everywhere.

**Date Display:** DESIGN.md specifies `"24 Oct 2026, 14:05 WAT"`. Need to verify all pages use this format.

**Name Rules (per docs/decisions.md):**
- Staff/audit/role-changes: `Full Name (email)` or email fallback
- Disbursement/payroll actor labels: full name only, `Unnamed user` for existing unnamed user, `Unknown user` when absent
- Member displays: role-dependent (committee sees only name; treasurer sees name + balances; etc.)
- **Risk:** Need to verify frontend consistently implements these fallbacks, not the API

---

## 4. Responsive & Accessibility Findings

### Already Implemented (Good)
- ✅ Visible 2px focus ring in primary color (`*:focus-visible` in index.css)
- ✅ 44px min touch targets on mobile (`@media (max-width: 639px)`)
- ✅ 16px font-size on mobile inputs (prevents iOS zoom)
- ✅ Reduced motion support (`@media (prefers-reduced-motion)`)
- ✅ Mobile drawer with Radix Dialog (sidebar below 1024px)
- ✅ ResponsiveTable component for mobile table conversion
- ✅ Keyboard navigation via Radix primitives

### Findings to Verify

| Issue | Risk | Verification Method |
|-------|------|---------------------|
| Wide tables on mobile | HIGH | Test each table page at 320px |
| Focus visible on all interactive elements | MEDIUM | Audit all buttons/links |
| ARIA labels on icon-only buttons | MEDIUM | Check each icon button usage |
| Color contrast (WCAG AA) | HIGH | Run contrast audit |
| Keyboard tab order | MEDIUM | Manual test or audit script |
| Form error messages linked to inputs | MEDIUM | Check Field.tsx and form pages |
| Screen reader table navigation | MEDIUM | Check Table.tsx for proper semantics |

---

## 5. Risk Register: Behaviors NOT to Break

### Role-Gated Actions (from docs/decisions.md)
- **Treasurer:** Disbursement prepare/record, withdrawal approve, payment confirm, payroll post/sign-off (when admin), cash deposit confirm
- **Officer:** Record payments, create loans, import payroll, submit disbursements
- **Admin:** Authorize disbursements, confirm paid, sign-off payroll, staff management, settings approval
- **Committee:** Loan decisions only
- **Auditor:** Read-only on all financial data, read audit logs

### Maker-Checker Rules (Critical)
- Requester cannot approve/reject their own request
- A different authorizer must confirm (button disabled with reason)
- Cash deposit: different treasurer from every receipt recorder in batch
- Disbursement: preparer ≠ authorizer, authorizer ≠ paid confirmer

### Name Display Rules (Critical)
- Audit/role-change views: `Full Name (email)` for authorized readers, email fallback
- Disbursement/payroll labels: full name only, `Unnamed user` (existing no-name), `Unknown user` (absent)
- Member detail: role-dependent fields (treasurer gets balances, committee gets name only)

### API Contracts (from openapi-fetch)
- All endpoints typed via `.d.mts` files
- `.mjs` files contain runtime logic
- **Convention MUST be preserved:** `.mjs` + `.d.mts` pair for each feature

### Notification Badges
- Menu badges count entity IDs from shared eligibility rules
- Badge count = union of eligible actions for user's role(s)
- Self-actions excluded from counts
- Pages: dashboard, loans (committee), payroll (treasurer/admin), disbursements, staff (admin role approvals)

### Frontend Tests Protecting These Behaviors
- `npm run test:roles` — 113 tests covering role-based access and visibility
- Individual `.test.mjs` files: accountNavigation, bankStatementsUpload, cooperativeSettings, dashboardPolicy, disbursementTimeline, loanSnapshotPresentation, memberActionForm, navigation, passwordNotice, paymentForm, paymentNoConfirmer, payrollPolicy, settingsReview, sharePurchasePolicy, shareRegister

**CRITICAL:** Any redesign must pass:
```bash
cd frontend && npm run typecheck && npm run build && npm run test:roles && node --test
```

---

## 6. Proposed Design Direction (≤15 lines)

1. **Tone:** Calm, exact, accountable — like a well-kept ledger, never casual fintech. Direct language, sentence case, Nigerian/British English.
2. **Palette:** Keep current — #0E2F2B primary, #F2F2EC background, #E0B04F gold accents. Money NOT coloured green/red — use sign + wording.
3. **Typography:** Inter for UI, Source Serif 4 (600) for page titles. Tight tracking on large serif titles. Tabular nums always on money/dates.
4. **Spacing:** 8px grid. Generous padding: cards 24px, sections 32–48px. Kill border soup — use surface contrast, not borders.
5. **Components:**
   - Primary button: solid #0E2F2B, 40px tall, 8px radius
   - Secondary: ghost/outline
   - Tertiary: minimal/link
   - Tables: responsive — stack as cards below md
   - Badges: icon + text + color (never color alone)
6. **Motion:** 150ms ease-out, respect reduced-motion. No decorative animation.
7. **Accessibility:** WCAG AA always. Focus visible. 44px touch targets. Proper labels.

---

## 7. Implementation Plan

### Batch 1: Tokens & Base Components
**Files:** `src/index.css`, `src/ui/*.tsx`  
**Risk:** LOW — foundational, widely used  
**Tests:** `npm run typecheck`, `npm run build`  
**Checklist:**
- [ ] Verify all DESIGN.md tokens in @theme block
- [ ] Audit Button variants (primary/secondary/tertiary/destructive)
- [ ] Audit Badge variants (status + count pills)
- [ ] Audit Table ResponsiveTable usage
- [ ] Audit empty states on all list pages
- [ ] Audit loading skeletons on all async pages

### Batch 2: Navigation & Layout Shell
**Files:** `src/App.tsx`, `src/navigation.js`, sidebar components  
**Risk:** MEDIUM — affects all authenticated pages  
**Tests:** `npm run test:roles`, navigation tests  
**Checklist:**
- [ ] Verify sidebar 240px / drawer below 1024px
- [ ] Verify breadcrumb + title + action header pattern
- [ ] Audit badge counts per role
- [ ] Audit mobile drawer behavior (focus trap, body scroll lock)

### Batch 3: Shared States (Loading, Empty, Error, Success)
**Files:** `src/ui/Feedback.tsx`, page components  
**Risk:** MEDIUM — affects user perception of robustness  
**Tests:** visual verification, manual testing  
**Checklist:**
- [ ] Standardize loading skeleton per component geometry
- [ ] Standardize empty state (icon + message + CTA)
- [ ] Standardize error banner (non-blocking, retry CTA)
- [ ] Standardize success feedback (toast/badge)
- [ ] Audit overflow states (truncate, line-clamp)

### Batch 4: Members & Loans (Highest Traffic)
**Files:** `MembersPage.tsx`, `MemberDetailPage.tsx`, `LoansPage.tsx`, `LoanDetailPage.tsx`  
**Risk:** MEDIUM-HIGH — core business data, role-dependent fields  
**Tests:** `npm run test:roles`, member/loan .mjs tests  
**Checklist:**
- [ ] Verify role-dependent field display
- [ ] Verify name fallback: "Unnamed user", "Unknown user"
- [ ] Verify money format: ₦X,XXX.XX
- [ ] Verify date format: "24 Oct 2026, 14:05 WAT"
- [ ] Audit table responsive behavior
- [ ] Audit timeline component on loan detail

### Batch 5: Payments & Disbursements (Maker-Checker)
**Files:** `PaymentsPage.tsx`, `PaymentRecordForm.tsx`, `DisbursementsPage.tsx`, `DisbursementDetailPage.tsx`  
**Risk:** HIGH — financial transactions, separation of duties  
**Tests:** `npm run test:roles`, payment/disbursement tests  
**Checklist:**
- [ ] Verify disabled buttons show reason ("A different authorizer must approve")
- [ ] Verify bank statement import flow
- [ ] Verify cash deposit: different treasurer from all recorders
- [ ] Verify disbursement stepper: prepare → authorize → record → confirm
- [ ] Audit action eligibility badge counts

### Batch 6: Payroll (Complex Workflow)
**Files:** `PayrollPage.tsx`, `PayrollDetailPage.tsx`  
**Risk:** HIGH — payroll upload, matching, sign-off  
**Tests:** `npm run test:roles`, payroll tests  
**Checklist:**
- [ ] Verify upload → dry-run → import → post → sign-off stepper
- [ ] Verify exception resolution flow
- [ ] Verify sign-off: different from poster
- [ ] Audit actor label: full name only, "Unnamed user", "Unknown user"

### Batch 7: Settings, Staff, Audit (Admin Only)
**Files:** `CooperativeSettingsPage.tsx`, `StaffPage.tsx`, `AuditPage.tsx`  
**Risk:** MEDIUM — admin functions  
**Tests:** `npm run test:roles`, settings/staff tests  
**Checklist:**
- [ ] Verify admin-only access
- [ ] Verify settings approval workflow
- [ ] Verify staff role management
- [ ] Verify audit log display with "Verify chain" status
- [ ] Verify name format: "Full Name (email)"

### Batch 8: Polish & Accessibility Pass
**Files:** All pages, components  
**Risk:** LOW — refinement  
**Tests:** `npm run test:roles`, contrast audit, keyboard test  
**Checklist:**
- [ ] Run contrast audit (WCAG AA)
- [ ] Verify all interactive elements have focus-visible
- [ ] Audit touch targets (44px mobile)
- [ ] Audit ARIA labels on icon buttons
- [ ] Audit form error association

---

## Conflicts with Open Feature Branches

**Current open PRs:** None (checked via `gh pr list --state open`)

No active feature branches to conflict with. Safe to proceed with any batch.

---

## Next Action

Await owner approval of this audit plan before starting Batch 1 (Tokens & Base Components).