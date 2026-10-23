"""Needs a real Postgres with the baseline migration and roles from scripts/neon_roles.sql.

export DATABASE_URL=...   DATABASE_URL_DIRECT=...
pytest -m integration
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

needs_db = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and os.environ.get("DATABASE_URL_DIRECT")),
    reason="DATABASE_URL and DATABASE_URL_DIRECT not set",
)


@needs_db
def test_smoke_script_passes():
    here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    proc = subprocess.run(
        [sys.executable, "scripts/neon_smoke_test.py"],
        cwd=here,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
