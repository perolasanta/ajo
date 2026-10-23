.PHONY: setup api test lint fmt migrate smoke web
PY=backend/.venv/bin
setup:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend && npm install
api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
web:
	cd frontend && npm run dev
test:
	cd backend && .venv/bin/pytest tests/unit -q
lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
migrate:
	cd backend && .venv/bin/alembic upgrade head
smoke:
	cd backend && .venv/bin/python scripts/neon_smoke_test.py
