.DEFAULT_GOAL := help

ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BACKEND := $(ROOT)backend
FRONTEND := $(ROOT)frontend
PYTHON := $(BACKEND)/.venv/bin/python
RUFF := $(BACKEND)/.venv/bin/ruff
MYPY := $(BACKEND)/.venv/bin/mypy
ALEMBIC := $(BACKEND)/.venv/bin/alembic

.PHONY: help setup api web test test-all test-db-up test-db-down lint fmt check-fast check hooks migrate smoke restore-drill

help:
	@printf '%s\n' 'check-fast  Run database-free backend and frontend checks.' 'check       Run check-fast, database tests, build, and role tests.' 'fmt         Format backend and ops/backup safely with Ruff.' 'hooks       Configure this checkout to use .githooks.'

setup:
	cd $(ROOT) && cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd $(ROOT) && cd frontend && npm install

api:
	cd $(ROOT) && cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web:
	cd $(ROOT) && cd frontend && npm run dev

test:
	cd $(ROOT) && cd backend && .venv/bin/pytest -m "(unit or db) and not property" -q

test-all:
	cd $(ROOT) && cd backend && HYPOTHESIS_PROFILE=ci .venv/bin/pytest -q

test-db-up:
	cd $(ROOT) && docker compose -f ops/docker-compose.test.yml up -d --wait

test-db-down:
	cd $(ROOT) && docker compose -f ops/docker-compose.test.yml down --remove-orphans

lint:
	cd $(ROOT) && cd backend && .venv/bin/ruff check .
	cd $(ROOT) && cd ops/backup && ../../backend/.venv/bin/ruff check .
	cd $(ROOT) && cd backend && .venv/bin/ruff format --check .
	cd $(ROOT) && cd ops/backup && ../../backend/.venv/bin/ruff format --check .
	cd $(ROOT) && cd backend && .venv/bin/mypy app

fmt:
	cd $(ROOT) && $(RUFF) format backend ops/backup

check-fast:
	@set -e; \
	step() { name="$$1"; shift; printf 'CHECK-FAST: %s\n' "$$name"; if ! "$$@"; then printf 'CHECK-FAST: FAILED at %s\n' "$$name"; exit 1; fi; }; \
	step 'ruff check backend' sh -c 'cd "$(BACKEND)" && .venv/bin/ruff check .'; \
	step 'ruff check ops/backup' sh -c 'cd "$(ROOT)ops/backup" && ../../backend/.venv/bin/ruff check .'; \
	step 'ruff format --check backend' sh -c 'cd "$(BACKEND)" && .venv/bin/ruff format --check .'; \
	step 'ruff format --check ops/backup' sh -c 'cd "$(ROOT)ops/backup" && ../../backend/.venv/bin/ruff format --check .'; \
	step 'mypy app' sh -c 'cd "$(BACKEND)" && .venv/bin/mypy app'; \
	step 'import app.main' sh -c 'cd "$(BACKEND)" && .venv/bin/python -c "import app.main"'; \
	step 'migration heads' $(ROOT)scripts/check_migrations.sh; \
	step 'frontend typecheck' sh -c 'cd "$(FRONTEND)" && npm run typecheck'; \
	step 'frontend node tests' sh -c 'cd "$(FRONTEND)" && npm run test:roles'; \
	printf '%s\n' 'CHECK-FAST: PASSED'

check:
	@set -e; \
	cleanup() { $(MAKE) test-db-down >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT; \
	$(MAKE) check-fast; \
	printf '%s\n' 'CHECK: starting test database'; \
	$(MAKE) test-db-up; \
	printf '%s\n' 'CHECK: backend suite'; \
	output=$$(mktemp); \
	if ! (cd "$(BACKEND)" && .venv/bin/pytest -q | tee "$$output"); then printf '%s\n' 'CHECK: FAILED at backend suite'; exit 1; fi; \
	tests=$$(sed -nE 's/.*([0-9]+ passed).*/\1/p' "$$output" | tail -1); rm -f "$$output"; \
	printf '%s\n' 'CHECK: frontend build'; \
	(cd "$(FRONTEND)" && npm run build); \
	printf '%s\n' 'CHECK: frontend role tests'; \
	(cd "$(FRONTEND)" && npm run test:roles); \
	printf 'CHECK: PASSED (%s)\n' "$${tests:-unknown tests}"

hooks:
	cd $(ROOT) && git config core.hooksPath .githooks

migrate:
	cd $(ROOT) && cd backend && .venv/bin/alembic upgrade head

smoke:
	cd $(ROOT) && cd backend && .venv/bin/python scripts/neon_smoke_test.py

restore-drill:
	cd $(ROOT) && ./scripts/restore_drill.sh
