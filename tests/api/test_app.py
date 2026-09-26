import importlib
from collections import Counter
from importlib.metadata import version

import pytest
from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.os import AgentOS
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from app.main import create_app
from app.runtime import SMOKE_TEST_AGENT_ID, RuntimeConfigurationError
from app.runtime.non_executing_model import ModelExecutionDisabledError, NonExecutingModel
from tests.routes import effective_api_routes


def registered_agent(app: FastAPI, agent_id: str) -> Agent:
    (agent,) = [a for a in app.state.agent_os.agents if a.id == agent_id]
    return agent


PROVIDER_KEY_VARIABLES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")

    assert callable(module.create_app)


def test_application_boots(settings, runtime_settings) -> None:
    app = create_app(settings, runtime_settings)

    assert isinstance(app, FastAPI)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_health_reports_application_and_runtime(client, settings) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": {"name": settings.name, "version": __version__, "environment": "test"},
        "agent_runtime": {"framework": "agno", "version": version("agno"), "status": "ready"},
    }


def test_health_does_not_leak_configuration(client, settings, runtime_settings) -> None:
    body = client.get("/health").text

    assert runtime_settings.os_security_key not in body
    assert str(settings.database_url) not in body
    assert "postgresql" not in body


def test_agentos_is_attached_to_the_product_app(client) -> None:
    agent_os = client.app.state.agent_os

    assert isinstance(agent_os, AgentOS)
    assert agent_os.base_app is client.app
    assert agent_os.get_app() is client.app


def test_agentos_uses_postgres_db_in_agno_runtime_schema(client) -> None:
    db = client.app.state.agent_os.db

    assert isinstance(db, PostgresDb)
    assert db.db_schema == "agno_runtime"


def test_smoke_test_agent_is_registered(client, auth_headers) -> None:
    agent = registered_agent(client.app, SMOKE_TEST_AGENT_ID)

    assert isinstance(agent, Agent)
    assert not agent.tools
    response = client.get("/agents", headers=auth_headers)
    assert response.status_code == 200
    assert [a["id"] for a in response.json()] == [SMOKE_TEST_AGENT_ID]


def test_agno_version_is_pinned() -> None:
    import agno

    assert version("agno") == "3.0.11"
    assert "site-packages" in agno.__file__


def test_disabled_provider_boots_without_any_provider_key(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings
) -> None:
    for variable in PROVIDER_KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    app = create_app(settings, runtime_settings)
    agent_ids = [agent.id for agent in app.state.agent_os.agents]
    model = registered_agent(app, SMOKE_TEST_AGENT_ID).model

    assert settings.default_model_provider == "disabled"
    assert agent_ids == [SMOKE_TEST_AGENT_ID]
    assert isinstance(model, NonExecutingModel)
    with pytest.raises(ModelExecutionDisabledError):
        model.invoke()


def test_missing_database_url_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, runtime_settings
) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeConfigurationError, match="APP_DATABASE_URL"):
        create_app(Settings(_env_file=None), runtime_settings)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_os_security_key_is_rejected(settings, key) -> None:
    with pytest.raises(RuntimeConfigurationError, match="OS_SECURITY_KEY is required"):
        create_app(settings, AgnoAPISettings(os_security_key=key))


@pytest.mark.parametrize("key", ["123", "secret", "development", "x" * 31, " " + "x" * 31])
def test_short_os_security_key_is_rejected(settings, key) -> None:
    with pytest.raises(RuntimeConfigurationError, match="at least 32 characters"):
        create_app(settings, AgnoAPISettings(os_security_key=key))


@pytest.mark.parametrize("key", ["x" * 32, "0123456789abcdef" * 4])
def test_os_security_key_of_32_or_more_characters_is_accepted(settings, key) -> None:
    app = create_app(settings, AgnoAPISettings(os_security_key=key))

    with TestClient(app) as client:
        response = client.get("/agents", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200


class TestRouting:
    def test_product_health_route_is_preserved(self, client) -> None:
        health_routes = [
            route for route in client.app.routes if getattr(route, "path", None) == "/health"
        ]

        assert len(health_routes) == 1
        assert health_routes[0].endpoint.__module__ == "app.main"

    def test_agentos_routes_exist(self, client) -> None:
        paths = {path for _, path in effective_api_routes(client.app.routes)}

        assert {
            "/agents",
            "/agents/{agent_id}",
            "/sessions",
            "/sessions/{session_id}",
            "/info",
        } <= paths

    def test_no_duplicate_routes(self, client) -> None:
        counts = Counter(effective_api_routes(client.app.routes))

        assert [route for route, count in counts.items() if count > 1] == []
        assert counts[("GET", "/health")] == 1
