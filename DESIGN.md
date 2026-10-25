# Ajo: design system

## Principles
1. Clarity before decoration. Every number can explain itself (a "Why?" popover shows the formula).
2. Accountability is visible: who did what, when, with evidence. Separation of duties is shown, not hidden (disabled buttons say why).
3. Calm density: tables and forms are compact but never cramped.
4. Trust cues: exact amounts, real timestamps (WAT), short hashes for evidence, no vague wording.
5. Fast on slow networks: no heavy assets, self-hosted fonts, small JS.

## Avoid (looks AI-generated)
Purple or blue gradients, glassmorphism, big rounded cards everywhere, emoji icons, stock illustrations, neon fintech palettes, centered hero blocks, decorative animation, red/green on every number.

## Colour (CSS variables, light theme first; keep tokens so dark mode can come later)
bg #F2F2EC | surface #FFFFFF | border #E6E4DD | text #1C1917 | muted #6B665E
primary/sidebar #0E2F2B | primary-hover #17433D | primary-soft #E5ECE9 | muted gold #E0B04F (dark text only)
warning #B45309 on #FEF3C7 | danger #B42318 on #FEE4E2 | success #166534 on #DCFCE7 | info #1D4ED8 on #DBEAFE
Money is NOT coloured green/red. Use sign and wording. Status is colour plus icon plus text, never colour alone. All text meets WCAG AA.

## Type
Interface: Inter (variable, self-hosted), tabular numerals on, 14px base in tables, 15-16px elsewhere.
Page titles and large figures' labels: Source Serif 4, weight 600, for a ledger-book feel. Money figures stay in Inter tabular.
Scale: 12, 13, 14, 16, 20, 24, 30. Line heights 1.4-1.6. Self-host subsets (latin) and load only weights used.

## Layout
Desktop first (1280+), usable on tablet and phone. Sidebar 240px (drawer below 1024px), content max width 1200px, 24px page padding, 8px spacing grid. At under 1024px, use a 56px top bar and a Radix Dialog drawer: width min(86vw, 300px), safe-area aware, 200ms transform, dark overlay, focus trap, Esc/overlay close, navigation close and body-scroll lock. Respect reduced motion.
Page header: breadcrumb above a 32px tight-tracked serif title; action right on desktop and full-width on phones. One primary button per view. Put min-w-0 on flex/grid children; stack two-column layouts below lg; use dvh units and account for the mobile top bar in sticky offsets.
Cards are 12px radius, #E6E4DD 1px border and shadow 0 1px 2px rgba(20,30,28,.05). Buttons are 40px high and 8px radius. Badges are icon-bearing pills. Sidebar active nav has a soft white tint; count pills are muted gold with dark text. User block is pinned to sidebar bottom.

## Components and behaviour
Tables: header is #FAF9F6, 11px uppercase and tracked; cells have 12px/16px padding, hairline row borders and hover tint. Money is right-aligned, tabular, whitespace-nowrap and responsively sized. Use ResponsiveTable: md+ uses a normal table; below md, stack rows as cards with title, status badge and labelled key-value details. Columns declare title, badge, meta or hidden; never horizontal-scroll tables.
Money: always ₦ with 2 decimals and thousands separators; negatives with a minus sign.
Dates: "24 Oct 2026, 14:05 WAT"; relative time in a tooltip.
Forms: labels above fields, helper text, inline validation on blur, naira inputs with live formatting, phone as +234 format.
Dialogs: destructive or irreversible actions need a typed reason. Show consequences in plain words.
Maker-checker: when the user cannot act (they prepared it), show the button disabled with the reason ("A different authorizer must approve this").
Timeline (loan detail): vertical, each event shows actor, role, time, channel, and evidence (consent id, short hash with copy button).
Status badges: draft (pencil, neutral), awaiting confirmation (clock, amber), under review (eye, blue), approved (check, green), ready (arrow-circle-right, blue), disbursed (banknote, teal), repaying (repeat, teal), closed (lock, neutral and distinct from draft), disputed (alert-triangle, red), expired (clock-off, neutral).
Icons: lucide-react, 16/20px, 1.75 stroke. No emoji.
Motion: 150ms ease-out on hover, focus and dialogs only. Respect prefers-reduced-motion. No page transitions.
Accessibility: visible 2px focus ring in primary, targets at least 44px on phones, real labels, keyboard operable everything, aria on dialogs and tables. Inputs are 16px on phones to prevent iOS zoom. Dialogs and confirmations are full-width bottom sheets on phones (slide up, max-height 90dvh, internal scroll, large buttons). Member/detail status tabs wrap or become a select below sm. Login and MFA are single-column and fit 320px.

## Responsive review routes (development only)
`/design` remains the component review. `/design/shell` renders the real shell and Members UI with fake data without login. `/design/responsive` embeds that route at 320, 390, 768 and 1280px widths for media-query review.
Voice: direct and calm. Errors say what happened and what to do next. Never "Oops".

## The member confirmation page (the most important screen)
Mobile first, 360px wide, no sidebar, system font stack, under 100 KB JS.
Top: cooperative name. Then one clear card: amount, monthly deduction, number of instalments, total to repay, first deduction date, destination account (masked), reference. Key figures large.
Plain-language sentence: "You are confirming a loan of ₦X to be repaid by ₦Y a month for N months from your salary."
Buttons: Confirm (primary, full width) and "This is not mine" (outline, danger text) which asks "Are you sure?" before opening a dispute.
OTP: numeric keypad (inputmode numeric, autocomplete one-time-code), visible expiry countdown.
Trust note: "Ajo and your cooperative will never ask for your PIN or password in chat."

## Key screens
Dashboard (pending approvals, disputes, payroll exceptions, arrears, notification failures) | Members list and Member 360 | Loan pipeline and Loan detail with timeline and eligibility "Why?" breakdown | Disbursement desk (three steps: prepare, authorize, record transfer, confirm paid, as a stepper) | Payroll reconciliation (upload, match, exceptions, sign-off stepper) | Audit log with a "Verify chain" status.
