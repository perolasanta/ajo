from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_neon_owner_role_name_is_consistent_across_operating_docs() -> None:
    context = (REPOSITORY_ROOT / "docs/context.md").read_text()
    neon = (REPOSITORY_ROOT / "docs/neon.md").read_text()

    for document in (context, neon):
        assert "ajo_owner" not in document
        assert "Neon project owner role" in document
        assert "neondb_owner" in document
