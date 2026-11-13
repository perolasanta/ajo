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

## Test-writing rules

1. Copy the structure of the closest existing passing test in the same module and use the templates in `tests/README.md`.
2. Never assert on formatting you have not seen: do not parse Postgres output (constraint definitions render as `kind = ANY (ARRAY[...])`); use the helpers in `tests/helpers`; never compare a string with a regex using `strictEqual` or `==`; use `assert.match` or `re.search`.
3. Assert behaviour, not shapes that evolve: check the keys the feature owns instead of full-dict equality, match a stable phrase in messages, and use the shared constants for fallbacks (`"Unnamed user"`, `"Unknown user"`).
4. One definition per name: no repeated dictionary keys, no test function defined twice.
5. Tests must not depend on the environment: set required settings (`EMAIL_ENABLED`, `TELEGRAM_ENABLED`, `PUBLIC_BASE_URL` and similar) explicitly through a fixture, create your own cooperative per test, no ordering dependence, no real clock, no Hypothesis deadline on database tests.
6. Changing behaviour means changing its tests in the same commit: grep `tests/` and `frontend/src` for the old key, text, status or import and update every hit.
7. Red-first tests must fail for the intended reason and must never import names that do not exist yet in a module CI collects (put a minimal stub in the same commit), and a PR is never ready while collection is broken.
8. Run `make check-fast` before every commit that ends a step, and if you cannot run tests say so in the first line of your report and keep new tests to the templates.
9. When you cannot run database tests, use CI as the runner: push, read the failing job, fix, repeat, at most 5 cycles.

## Checks
Agents run `make check-fast` before every commit that ends a step. The owner runs `make check` before every push. Never report a task as finished if `make check-fast` fails. In the final report, list which checks could not run.

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

## Local agent mode (Kiro, Codex or any agent working in the owner's local clone)

This section applies when you work in a local clone without GitHub access. Where it conflicts with the rules above, this section prevails.

1. Work only on the branch the owner created. If the current branch is main, stop and say so. Never switch branches, never commit on main.
2. Commit locally after each numbered step with the message "step N: <what>". Do NOT push, do not open or edit pull requests, do not run gh. The owner pushes.
3. Never end a turn with uncommitted work. If your budget is running low, commit what exists ("wip: <what>") and print a STATE block: steps done, steps remaining, next action. An agent resuming the task reads `git log main..HEAD` and continues from the first unfinished step without redoing finished work.
4. The owner starts the test database before the session. You may run TARGETED tests only: `cd backend && source .venv/bin/activate && pytest <files or -k expression> -q 2>&1 | tail -25`, plus ruff, mypy, `python -c "import app.main"`, `alembic heads` (exactly one head), and for the frontend `npm run typecheck` and `node --test <file>`. Never run the whole suite, never start or stop Docker, never pull images, never install packages or dependencies, never use the network except git operations the owner approved.
5. Never open, print or copy .env files or anything that looks like a secret. If a task seems to need one, stop and ask.
6. Ask before any command that deletes files, rewrites git history or changes files outside this repository.
