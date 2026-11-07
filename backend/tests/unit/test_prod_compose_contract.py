"""Static, no-Docker contract tests for the production Compose topology."""

from __future__ import annotations

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
COMPOSE = ROOT / "ops/docker-compose.prod.yml"


def service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|^networks:\n|\Z)",
        compose,
    )
    if match is None:
        raise AssertionError(f"missing Compose service {service}")
    return match.group(1)


class ProductionComposeContractTests(unittest.TestCase):
    def test_compose_uses_project_specific_names_and_generated_container_names(self) -> None:
        compose = COMPOSE.read_text()
        self.assertRegex(compose, r"(?m)^name: perolasanta-ajo-prod$")
        self.assertRegex(compose, r"(?m)^\s+name: perolasanta-ajo-prod_net$")
        self.assertNotIn("container_name:", compose)

    def test_all_published_ports_are_loopback_only(self) -> None:
        compose = COMPOSE.read_text()
        host_addresses = re.findall(r"(?m)^\s+-\s+([^:\s]+):\$\{AJO_[A-Z_]+_PORT", compose)
        self.assertTrue(host_addresses)
        self.assertTrue(all(address == "127.0.0.1" for address in host_addresses))

    def test_api_waits_for_database_and_uses_database_readiness_healthcheck(self) -> None:
        api = service_block(COMPOSE.read_text(), "api")
        self.assertIn("python -m app.wait_for_db", api)
        self.assertIn("exec gunicorn", api)
        self.assertIn("/api/v1/health/db", api)

    def test_backend_processes_receive_public_base_url(self) -> None:
        compose = COMPOSE.read_text()
        for service in ("api", "worker", "scheduler", "migration"):
            with self.subTest(service=service):
                self.assertIn(
                    "PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-}",
                    service_block(compose, service),
                )

    def test_worker_and_scheduler_have_healthchecks(self) -> None:
        for service in ("worker", "scheduler"):
            with self.subTest(service=service):
                self.assertIn("healthcheck:", service_block(COMPOSE.read_text(), service))
