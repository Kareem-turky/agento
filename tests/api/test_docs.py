"""API docs exposure policy: served in local/test, absent in staging/production."""

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.product_auth import deployment_settings

DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def build_client(settings, environment: str, docs_enabled: bool = True) -> TestClient:
    runtime_settings = AgnoAPISettings(
        os_security_key=TEST_OS_SECURITY_KEY, docs_enabled=docs_enabled
    )
    if environment in ("staging", "production"):
        # Deployments always run with Product authentication configured.
        configured = deployment_settings(settings, environment)
    else:
        configured = settings.model_copy(update={"environment": environment})
    return TestClient(create_app(configured, runtime_settings))


@pytest.mark.parametrize("environment", ["local", "test"])
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_served_in_development_environments(settings, environment, path) -> None:
    with build_client(settings, environment) as client:
        assert client.get(path).status_code == 200
        assert client.app.state.agent_os.settings.docs_enabled is True


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_absent_outside_development(settings, environment, path) -> None:
    # Even if Agno's DOCS_ENABLED is left on, the environment policy wins.
    with build_client(settings, environment, docs_enabled=True) as client:
        assert client.get(path).status_code == 404
        assert client.app.state.agent_os.settings.docs_enabled is False


def test_local_docs_can_be_disabled_with_agno_setting(settings) -> None:
    with build_client(settings, "local", docs_enabled=False) as client:
        for path in DOC_PATHS:
            assert client.get(path).status_code == 404


def test_production_keeps_health_public_and_agentos_protected(settings) -> None:
    with build_client(settings, "production") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/agents").status_code == 401
        headers = {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
        assert client.get("/agents", headers=headers).status_code == 200
