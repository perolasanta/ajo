"""Tests for runtime dependency completeness."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"


# Known mapping from import names to distribution/package names
# Mapping from import name to distribution name (when they differ)
IMPORT_TO_DISTRIBUTION = {
    "multipart": "python-multipart",
    "jose": "python-jose",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    # Transitive deps that FastAPI/pydantic bring in - map to the main package
    "starlette": "fastapi",
    "pydantic_settings": "pydantic-settings",
    "dotenv": "pydantic-settings",  # pydantic-settings bundles python-dotenv
    "argon2": "argon2-cffi",
    "httpx": "httpx",
}


def _get_runtime_dependencies() -> set[str]:
    """Read runtime dependencies from pyproject.toml."""
    pyproject = BACKEND_ROOT / "pyproject.toml"
    content = pyproject.read_text()
    # Find the dependencies list
    lines = content.split("\n")
    in_deps = False
    deps = set()
    for line in lines:
        if line.strip().startswith("dependencies = ["):
            in_deps = True
            continue
        if in_deps:
            if line.strip() == "]":
                break
            # Extract package name (before >=, ==, etc.)
            pkg = line.strip().strip(",").strip('"').strip("'")
            if pkg:
                # Handle "package[extra]>=version" format
                base_pkg = pkg.split("[")[0].split(">")[0].split("=")[0].split("<")[0]
                deps.add(base_pkg)
    return deps


def _get_stdlib_modules() -> set[str]:
    """Get standard library module names."""
    return sys.stdlib_module_names


def _scan_imports_in_file(file_path: Path) -> set[str]:
    """Scan a Python file for top-level third-party imports."""
    try:
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
    except SyntaxError:
        return set()

    imports = set()
    stdlib = _get_stdlib_modules()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod not in stdlib and not mod.startswith("app"):
                    imports.add(mod)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                if mod not in stdlib and not mod.startswith("app"):
                    imports.add(mod)

    return imports


def _map_import_to_distribution(import_name: str) -> str:
    """Map import name to distribution name."""
    return IMPORT_TO_DISTRIBUTION.get(import_name, import_name)


class TestRuntimeDependencies:
    """Tests for runtime dependency completeness."""

    def test_python_multipart_is_runtime_dependency(self):
        """python-multipart must be in runtime dependencies (for request.form())."""
        runtime_deps = _get_runtime_dependencies()
        assert "python-multipart" in runtime_deps, (
            "python-multipart is required for FastAPI form parsing (request.form())"
        )

    def test_asyncpg_is_runtime_dependency(self):
        """asyncpg must be in runtime dependencies (for database connections)."""
        runtime_deps = _get_runtime_dependencies()
        assert "asyncpg" in runtime_deps, "asyncpg is required for database connections"

    def test_httpx_is_runtime_dependency(self):
        """httpx must be in runtime dependencies (used by worker for HTTP calls)."""
        runtime_deps = _get_runtime_dependencies()
        assert "httpx" in runtime_deps, "httpx is required for worker HTTP calls"


class TestImportDependencyCoverage:
    """Test that all third-party imports are covered by runtime dependencies."""

    def test_all_app_imports_are_runtime_deps(self):
        """Every third-party import in backend/app must be a runtime dependency."""
        runtime_deps = _get_runtime_dependencies()
        app_dir = BACKEND_ROOT / "app"

        all_imports = set()
        for py_file in app_dir.rglob("*.py"):
            if "migrations" in py_file.parts:
                continue
            imports = _scan_imports_in_file(py_file)
            all_imports.update(imports)

        # Map imports to distributions
        missing = []
        for imp in sorted(all_imports):
            dist = _map_import_to_distribution(imp)
            if dist not in runtime_deps:
                missing.append(f"{imp} -> {dist}")

        assert not missing, f"Found imports without runtime dependencies: {missing}"


class TestBankStatementsEndpoint:
    """Test that bank statements endpoint works with multipart uploads."""

    def test_preview_endpoint_accepts_multipart(self):
        """Bank statements preview endpoint should accept multipart form data."""
        from io import BytesIO

        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)

        # Create a small CSV content
        csv_content = b"date,narration,amount\n2026-01-01,Test,1000\n"

        # Create multipart form data
        files = {
            "file": ("test.csv", BytesIO(csv_content), "text/csv"),
        }
        data = {
            "mapping": "date,credit,reference",
        }

        # Call the preview endpoint - should not return 500
        response = client.post(
            "/api/v1/bank-statements/preview?filename=test.csv",
            files=files,
            data=data,
        )

        # Should be 2xx or 4xx, never 500 (internal server error)
        assert response.status_code < 500, (
            f"Expected success or client error, got {response.status_code}: {response.text[:200]}"
        )
