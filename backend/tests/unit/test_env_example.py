"""Contract tests for the checked-in environment template (never reads .env)."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:  # Permit the stdlib runner in minimal local environments.
    pytest = None

if pytest is not None:
    pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


class EnvExampleTests(unittest.TestCase):
    def test_every_app_and_deployment_name_has_an_empty_commented_entry(self) -> None:
        settings_tree = ast.parse((ROOT / "backend/app/core/config.py").read_text())
        settings_names = {
            node.target.id.upper()
            for cls in settings_tree.body
            if isinstance(cls, ast.ClassDef) and cls.name == "Settings"
            for node in cls.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        compose_names = set(
            re.findall(
                r"\$\{([A-Z][A-Z0-9_]*)",
                (ROOT / "ops/docker-compose.prod.yml").read_text(),
            )
        )
        expected_names = settings_names | compose_names
        template_lines = (ROOT / ".env.example").read_text().splitlines()
        entries: dict[str, tuple[str, int]] = {}
        for index, line in enumerate(template_lines):
            match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
            if match:
                entries[match.group(1)] = (match.group(2), index)

        missing = expected_names - entries.keys()
        self.assertFalse(missing, f"missing environment names: {sorted(missing)}")
        for name, (value, index) in entries.items():
            self.assertEqual(value, "", f"{name} must have an empty value")
            self.assertGreater(index, 0, f"{name} must have a preceding comment")
            self.assertTrue(
                template_lines[index - 1].startswith("# "),
                f"{name} must have a one-line comment",
            )
        self.assertIn("BACKUP_AGE_RECIPIENTS", entries)
        self.assertNotIn("BACKUP_GPG_RECIPIENT", entries)
