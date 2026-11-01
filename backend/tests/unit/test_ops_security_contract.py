from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]


def test_production_compose_scopes_database_credentials_by_service():
    compose = yaml.safe_load((ROOT / "ops" / "docker-compose.prod.yml").read_text())
    services = compose["services"]

    assert all("env_file" not in service for service in services.values())
    assert "DATABASE_URL" in services["api"]["environment"]
    assert "DATABASE_URL_DIRECT" not in services["api"]["environment"]
    assert "DATABASE_URL" in services["worker"]["environment"]
    assert "DATABASE_URL_DIRECT" not in services["worker"]["environment"]
    assert "DATABASE_URL_SCHEDULER" in services["scheduler"]["environment"]
    assert "DATABASE_URL_DIRECT" not in services["scheduler"]["environment"]
    assert services["migration"]["environment"]["DATABASE_URL_DIRECT"].startswith(
        "${DATABASE_URL_DIRECT:"
    )


def test_restore_acl_script_has_idempotent_acl_repair_steps():
    script = (ROOT / "ops" / "backup" / "restore_app_acl.sh").read_text()

    assert "REVOKE ALL PRIVILEGES ON ALL TABLES" in script
<<<<<<< HEAD
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO ajo_app" in script
=======
    assert "GRANT SELECT ON ALL TABLES" in script
>>>>>>> origin/main
    assert "GRANT INSERT ON TABLE public.%I TO ajo_app" in script
    assert "GRANT UPDATE ON TABLE" in script
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES" in script
    assert "GRANT EXECUTE ON ALL FUNCTIONS" in script
    assert "ALTER DEFAULT PRIVILEGES" in script
