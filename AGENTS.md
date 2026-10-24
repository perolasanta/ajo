# Agent rules for Ajo
Read docs/context.md before any task. It is the source of truth.
- Sections 2 (decisions), 9-10 (data and business rules), 12 (security) are binding. Never change them without my explicit approval; propose the change and wait.
- You may update status, checklists, the roadmap progress and the Change log in docs/context.md as you work. Every edit to that file gets a dated line in its Change log.
- Never use float for money. Never UPDATE or DELETE ledger or audit rows. No financial logic in routers, bots or React.
- Tenant context only via app.core.db.transaction(). Never use SECURITY DEFINER for functions that touch tenant tables (the owner role bypasses RLS on Neon).
- Database access: only the dev branch URL from .env. Never ask for or use production credentials, the Neon API key or the Neon MCP server.
- One migration or feature slice per change, with tests. Run lint, mypy and tests before saying a task is done, and report the results honestly.
- If this file, context.md and the code disagree, stop and tell me.
## Cost and test discipline
- Read only the sections of docs/context.md that the task names. Do not re-read the whole file.
- Tests never run against Neon. Database tests use a local Postgres (see make test). Neon is only for `make migrate` on dev and `make smoke`.
- While working, run only the tests for the code you touched (pytest path::test or -k). Run `make test` once at the end. `make test-all` only when I ask.
- If a test fails twice for the same reason, stop and report instead of trying a third fix.
- Write only the tests the task lists. Do not add extra tests.
- Final report: at most 12 lines (what changed, test result, anything I must decide).