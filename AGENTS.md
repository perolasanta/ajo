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
## Run mode (permanent)
Never touch Docker. Never run pytest or make migrate; the owner runs them and pastes failures. Run only ruff and mypy. Do not explore the repo: open only the files named in the task. Final report: max 6 lines, then the exact commands the owner should run. No explanations of code.## Frontend format
Write code in normal formatted style, never whole files on one line. Frontend uses Prettier (npm run format). Before saying a frontend task is done, the code must pass `npm run typecheck`.

## Checkpoint rules (owner requirement: work can be interrupted at any time)

1. First action of every task: create the task branch from the latest main, push it with an empty commit (`git commit --allow-empty -m "chore: start <task>"`), and open a DRAFT pull request whose description lists the task's numbered steps as a checklist.
2. After each numbered step, or each unit of work that stands alone (a migration, a backend slice, a page, a set of tests), commit with the message "step N: <what>" and push immediately. Never end a turn with uncommitted work.
3. After each push, tick the matching checklist item in the PR description (`gh pr edit --body-file`) and keep a one-line "STATE:" at the top.
4. Failing tests written first may be committed with the prefix "test(red):", unfinished code with "wip:". Never present unfinished work as finished.
5. If your budget is running low, stop at a clean point: commit and push everything and write in the PR description "STOPPED AT step N; remaining: ...; next action: ...".
6. When resuming a task, read the PR description and `git log main..HEAD`, continue from the first unchecked step, and do not redo finished work.
7. Leave the PR as a draft. The owner marks it ready and merges after running the tests.
8. Task prompts may list test commands. Where they conflict with this file, this file prevails. Do not stop to ask.
