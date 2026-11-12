from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).parents[2]
TESTS_ROOT = BACKEND_ROOT / "tests"
ACTION_SPECS_PATH = BACKEND_ROOT / "app" / "domains" / "notifications" / "eligibility.py"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _constant_assignments(tree: ast.Module) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return values


def test_action_specs_has_no_repeated_keys() -> None:
    tree = _module(ACTION_SPECS_PATH)
    constants = _constant_assignments(tree)
    action_specs = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "ACTION_SPECS" for target in node.targets
        )
    )
    assert isinstance(action_specs.value, ast.Dict)
    keys = [
        constants.get(key.id, key.id) if isinstance(key, ast.Name) else ast.literal_eval(key)
        for key in action_specs.value.keys
        if key is not None
    ]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    assert not duplicates, f"repeated ACTION_SPECS keys: {duplicates}"


def test_test_function_names_are_unique_per_module() -> None:
    duplicates: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        names = [
            node.name
            for node in _module(path).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        duplicates.extend(
            f"{path.relative_to(TESTS_ROOT)}::{name}"
            for name, count in Counter(names).items()
            if count > 1
        )
    assert not duplicates, f"duplicate test function names: {duplicates}"
