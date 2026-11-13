from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).parents[2]
APP_ROOT = BACKEND_ROOT / "app"
ELIGIBILITY = APP_ROOT / "domains" / "notifications" / "eligibility.py"


def _constant_values(tree: ast.Module) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    values[target.id] = node.value.value
    return values


def _action_spec_keys() -> set[str]:
    tree = ast.parse(ELIGIBILITY.read_text(), filename=str(ELIGIBILITY))
    constants = _constant_values(tree)
    action_specs = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "ACTION_SPECS" for target in node.targets
        )
    )
    assert isinstance(action_specs.value, ast.Dict)
    keys: set[str] = set()
    for key in action_specs.value.keys:
        if isinstance(key, ast.Name):
            keys.add(constants[key.id])
        else:
            value = ast.literal_eval(key)
            assert isinstance(value, str)
            keys.add(value)
    return keys


def test_every_action_spec_has_an_enqueue_call_site() -> None:
    eligibility_tree = ast.parse(ELIGIBILITY.read_text(), filename=str(ELIGIBILITY))
    constants = _constant_values(eligibility_tree)
    wired: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "enqueue_action_notifications_safely" or len(node.args) < 3:
                continue
            event = node.args[2]
            if isinstance(event, ast.Constant) and isinstance(event.value, str):
                wired.add(event.value)
            elif isinstance(event, ast.Name) and event.id in constants:
                wired.add(constants[event.id])
            elif (
                isinstance(event, ast.Attribute)
                and isinstance(event.value, ast.Name)
                and event.value.id == "eligibility"
                and event.attr in constants
            ):
                wired.add(constants[event.attr])

    missing = sorted(_action_spec_keys() - wired)
    assert not missing, f"ACTION_SPECS kinds without enqueue call sites: {missing}"
