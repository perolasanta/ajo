#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BACKEND="$ROOT/backend"
if [[ -x "$BACKEND/.venv/bin/python" ]]; then
  PYTHON="$BACKEND/.venv/bin/python"
else
  PYTHON=python3
fi

cd "$BACKEND"
heads=$($PYTHON -m alembic heads 2>&1) || {
  printf 'MIGRATIONS: FAILED: alembic heads could not be read\n%s\n' "$heads" >&2
  exit 1
}
head_count=$(printf '%s\n' "$heads" | grep -Ec '\(head\)$' || true)
if [[ "$head_count" -ne 1 ]]; then
  printf 'MIGRATIONS: FAILED: expected exactly one Alembic head, found %s\n%s\n' "$head_count" "$heads" >&2
  exit 1
fi

if ! "$PYTHON" - "$BACKEND/migrations/versions" <<'PY'
import ast
import pathlib
import sys

versions_dir = pathlib.Path(sys.argv[1])
files = sorted(versions_dir.glob("*.py"))
revisions: dict[str, pathlib.Path] = {}
prefixes: dict[str, pathlib.Path] = {}
parents: dict[pathlib.Path, str | tuple[str, ...] | None] = {}

for path in files:
    prefix = path.name.split("_", 1)[0]
    if not prefix.isdigit():
        continue
    previous = prefixes.setdefault(prefix, path)
    if previous != path:
        raise SystemExit(f"duplicate numeric migration prefix {prefix}: {previous.name}, {path.name}")
    tree = ast.parse(path.read_text(), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
    revision = values.get("revision")
    if not isinstance(revision, str):
        raise SystemExit(f"{path.name}: missing string revision")
    if revision in revisions:
        raise SystemExit(f"duplicate migration revision {revision}: {revisions[revision].name}, {path.name}")
    revisions[revision] = path
    down_revision = values.get("down_revision")
    if down_revision is not None and not isinstance(down_revision, (str, tuple)):
        raise SystemExit(f"{path.name}: invalid down_revision")
    parents[path] = down_revision

for path, down_revision in parents.items():
    values = () if down_revision is None else (down_revision,) if isinstance(down_revision, str) else down_revision
    for parent in values:
        if parent not in revisions:
            raise SystemExit(f"{path.name}: down_revision {parent!r} does not exist")
PY
then
  printf '%s\n' 'MIGRATIONS: FAILED: static migration metadata check' >&2
  exit 1
fi

printf 'MIGRATIONS: PASSED: one Alembic head, unique numeric prefixes, valid down_revision links (no database used)\n'
