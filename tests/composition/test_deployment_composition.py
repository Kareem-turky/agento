"""Deployment composition root: environment policy, wiring graph and resource lifecycle.

No database is reached here: the Product engine is lazy, and unit settings point at an
address that refuses connections. Construction is observed by wrapping the names
``app.composition.local_mock`` builds with (the real classes still run).
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

import pytest
import sqlalchemy as sa
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.bootstrap import create_deployment_app
from app.composition import (
    DeploymentComposition,
    DeploymentCompositionError,
    build_deployment_composition,
    local_mock,
)
from app.config import Settings
from app.operations import OPERATIONS_ACTIONS
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import ScriptedToolModel

BUILT_NAMES = (
    "create_product_engine", "create_session_factory", "PostgresWriteCommandStore",
    "PostgresAuditSink", "ActionCatalog", "GovernanceGate", "MockCommerceSystem",
    "MockCommerceAdapter", "MockTicketDesk", "MockTicketingAdapter",
    "CreateOperationalTicketHandler", "ActionHandlerRegistry", "ExecutionCoordinator",
    "WriteCommandCoordinator", "build_operations_agent", "OperationsAgentRunner",
    "WriteCommandTicketService", "WriteCommandTicketQueryService", "DailyOperationsWorkflow",
    # Task 034: the daily report runs as the operations.daily_report Product Workflow.
    "build_default_workflow_catalog", "daily_report_registration", "WorkflowRuntimeRegistry",
    "PostgresWorkflowRunRepository", "WorkflowEngine", "WorkflowBackedDailyOperationsReportService",
)  # fmt: skip
MOCK_PROVIDER_NAMES = ("MockCommerceSystem", "MockCommerceAdapter", "MockTicketDesk",
                       "MockTicketingAdapter")  # fmt: skip


class Built:
    """Every object local_mock constructed: name -> [(object, args, kwargs)]."""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple[Any, tuple, dict]]] = defaultdict(list)
        self.disposed: list[sa.Engine] = []

    def one(self, name: str) -> tuple[Any, tuple, dict]:
        (call,) = self.calls[name]
        return call

    def obj(self, name: str) -> Any:
        return self.one(name)[0]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Built:
    record = Built()
    for name in BUILT_NAMES:
        original = getattr(local_mock, name)

        def wrapper(*args, _name=name, _original=original, **kwargs):
            obj = _original(*args, **kwargs)
            record.calls[_name].append((obj, args, kwargs))
            if _name == "create_product_engine":
                sa.event.listen(obj.sync_engine, "engine_disposed", record.disposed.append)
            return obj

        monkeypatch.setattr(local_mock, name, wrapper)
    return record


def mock_settings(settings: Settings, environment: str = "test", **updates) -> Settings:
    return Settings(_env_file=None, **(settings.model_dump() | {
        "environment": environment, "business_backend": "mock"} | updates))  # fmt: skip


def disabled_settings(settings: Settings, environment: str = "test") -> Settings:
    return Settings(_env_file=None, **(settings.model_dump() | {"environment": environment}))


# ----- configuration --------------------------------------------------------------


def test_business_backend_defaults_to_disabled_and_validates_syntax_only(monkeypatch):
    """Task 022: Settings check the id's shape; the registry decides what is installed."""
    assert Settings(_env_file=None).business_backend == "disabled"
    monkeypatch.setenv("APP_BUSINESS_BACKEND", "mock")
    assert Settings(_env_file=None).business_backend == "mock"
    for valid in ("disabled", "mock", "test-backend", "not-registered", "erp.v2"):
        assert Settings(_env_file=None, business_backend=valid).business_backend == valid
    for bad in ("MOCK", "", "memory backend", "../mock", "app.module:Class"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, business_backend=bad)


# ----- environment policy -----------------------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("backend", ["disabled", "mock"])
def test_deployments_fail_closed_before_anything_is_built(settings, built, environment, backend):
    s = deployment_settings(settings, environment, business_backend=backend)
    with pytest.raises(DeploymentCompositionError) as info:
        build_deployment_composition(s, model=ScriptedToolModel())
    assert str(info.value) in (
        "no business backend is available for staging/production deployments",
        "selected business backend is not allowed in this environment",
    )
    assert info.value.__cause__ is None
    assert dict(built.calls) == {}  # no engine, no mock provider, no service
    # And through the deployment factory: never an application.
    with pytest.raises(DeploymentCompositionError):
        create_deployment_app(s, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
                              model=ScriptedToolModel())  # fmt: skip
    assert dict(built.calls) == {}


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_mock_in_a_deployment_never_constructs_a_mock_provider(settings, monkeypatch, environment):
    def explode(*args, **kwargs):
        raise AssertionError("a mock provider was constructed")

    for name in MOCK_PROVIDER_NAMES:
        monkeypatch.setattr(local_mock, name, explode)
    s = deployment_settings(settings, environment, business_backend="mock")
    with pytest.raises(DeploymentCompositionError, match="not allowed in this environment"):
        create_deployment_app(s, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
                              model=ScriptedToolModel())  # fmt: skip


def test_local_mock_builder_refuses_deployments_itself(settings, built) -> None:
    s = deployment_settings(settings, "production", business_backend="mock")
    with pytest.raises(DeploymentCompositionError, match="not allowed in this environment"):
        local_mock.build_local_mock_composition(s, model=ScriptedToolModel())
    assert dict(built.calls) == {}


@pytest.mark.parametrize("environment", ["local", "test"])
def test_disabled_development_composes_no_business_services(settings, built, environment):
    composition = build_deployment_composition(disabled_settings(settings, environment))
    assert composition == DeploymentComposition()
    assert dict(built.calls) == {}
    model = ScriptedToolModel()
    assert (
        build_deployment_composition(
            disabled_settings(settings, environment), model=model
        ).default_model
        is model
    )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_mock_without_a_model_fails_before_any_resource(settings, built, environment):
    s = mock_settings(settings, environment)
    assert s.default_model_provider == "disabled"
    with pytest.raises(DeploymentCompositionError) as info:
        build_deployment_composition(s)
    assert str(info.value) == "Operations model is required for mock business composition"
    assert dict(built.calls) == {}


def test_mock_without_a_database_url_fails_safely(settings, built) -> None:
    s = mock_settings(settings, database_url=None)
    with pytest.raises(DeploymentCompositionError, match="APP_DATABASE_URL is required"):
        build_deployment_composition(s, model=ScriptedToolModel())
    assert dict(built.calls) == {}


def test_provider_credentials_never_appear_in_model_errors(settings, monkeypatch) -> None:
    from app.runtime import ModelConfigurationError

    monkeypatch.setenv("OPENAI_API_KEY", "")
    s = mock_settings(settings, default_model_provider="openai", default_model_id="some-model")
    with pytest.raises(ModelConfigurationError) as info:
        build_deployment_composition(s)
    assert "OPENAI_API_KEY must be set" in str(info.value)


# ----- the mock composition graph ---------------------------------------------------


@pytest.mark.parametrize("environment", ["local", "test"])
def test_mock_composes_the_real_core_on_one_shared_mock_system(settings, built, environment):
    s, model = mock_settings(settings, environment), ScriptedToolModel()
    composition = build_deployment_composition(s, model=model)

    engine, args, _ = built.one("create_product_engine")
    assert args == (str(s.database_url),)
    sessions, args, _ = built.one("create_session_factory")
    assert args == (engine,)
    store, args, _ = built.one("PostgresWriteCommandStore")
    assert args == (sessions,)
    audit, args, _ = built.one("PostgresAuditSink")
    assert args == (sessions,)  # one engine, one session factory for both tables

    catalog, args, _ = built.one("ActionCatalog")
    assert args == (OPERATIONS_ACTIONS,)
    gate, args, _ = built.one("GovernanceGate")
    assert args == (catalog,)

    system = built.obj("MockCommerceSystem")  # exactly one provider system
    commerce, args, _ = built.one("MockCommerceAdapter")
    assert args == (system,)
    desk = built.obj("MockTicketDesk")
    ticketing, args, _ = built.one("MockTicketingAdapter")
    assert args == (system, desk)  # same system: same canonical company/stores

    handler, args, _ = built.one("CreateOperationalTicketHandler")
    assert args == (ticketing,)
    registry, args, _ = built.one("ActionHandlerRegistry")
    assert args == ([handler],)
    coordinator, args, _ = built.one("ExecutionCoordinator")
    assert args == (gate, registry, audit)
    commands, args, _ = built.one("WriteCommandCoordinator")
    assert args == (store, coordinator, catalog)

    workflow, args, kwargs = built.one("DailyOperationsWorkflow")  # exactly one instance
    # The report reads through the SAME adapter and gate (no second mock system).
    assert args == () and kwargs == {"commerce": commerce, "gate": gate}

    # Task 034: that workflow is the ONE Step of operations.daily_report, executed by the
    # Workflow Platform over the SAME session factory (no second engine).
    catalog, args, _ = built.one("build_default_workflow_catalog")
    registration, args, _ = built.one("daily_report_registration")
    assert args == (workflow,)
    bindings, args, _ = built.one("WorkflowRuntimeRegistry")
    assert args == (catalog, [registration])
    runs, args, _ = built.one("PostgresWorkflowRunRepository")
    assert args == (sessions,)
    platform, args, kwargs = built.one("WorkflowEngine")
    assert args == (catalog, bindings, runs) and kwargs == {}
    daily, args, _ = built.one("WorkflowBackedDailyOperationsReportService")
    assert args == (platform,)

    agent, args, kwargs = built.one("build_operations_agent")
    assert args == (model,)
    # The agent's report tool is bound to that SAME report service.
    assert kwargs == {"commerce": commerce, "gate": gate, "coordinator": coordinator,
                      "daily_operations": daily}  # fmt: skip
    runner, args, _ = built.one("OperationsAgentRunner")
    assert args == (agent,)
    ticket_service, args, _ = built.one("WriteCommandTicketService")
    assert args == (commands,)
    query_service, args, _ = built.one("WriteCommandTicketQueryService")
    assert args == (store,)  # the same store is the durable reader

    assert composition.daily_operations_service is daily  # HTTP and agent share it

    assert composition.operations_service is runner
    assert composition.operations_ticket_service is ticket_service
    assert composition.operations_ticket_query_service is query_service
    assert composition.default_model is model
    # Only Product service contracts and lifecycle callables leave the composition.
    assert set(vars(composition)) == {
        "default_model", "operations_service", "operations_ticket_service",
        "operations_ticket_query_service", "daily_operations_service", "close", "discard",
    }  # fmt: skip

    # The engine is released exactly once, however often close is called.
    asyncio.run(composition.close())
    asyncio.run(composition.close())
    composition.discard()
    assert built.disposed == [engine.sync_engine]


def test_partial_composition_failure_releases_the_engine(settings, built, monkeypatch):
    def broken_agent(*args, **kwargs):
        raise RuntimeError("agent construction failed")

    monkeypatch.setattr(local_mock, "build_operations_agent", broken_agent)
    with pytest.raises(RuntimeError, match="agent construction failed"):
        build_deployment_composition(mock_settings(settings), model=ScriptedToolModel())
    engine = built.obj("create_product_engine")
    assert built.disposed == [engine.sync_engine]


def test_app_construction_failure_releases_the_engine(settings, built) -> None:
    from app.runtime import RuntimeConfigurationError

    with pytest.raises(RuntimeConfigurationError):  # no OS_SECURITY_KEY
        create_deployment_app(mock_settings(settings), AgnoAPISettings(os_security_key=None),
                              model=ScriptedToolModel())  # fmt: skip
    engine = built.obj("create_product_engine")
    assert built.disposed == [engine.sync_engine]


def test_release_failure_is_safe(settings, built, monkeypatch, caplog) -> None:
    composition = build_deployment_composition(mock_settings(settings), model=ScriptedToolModel())
    engine = built.obj("create_product_engine")

    async def failing_dispose(*args, **kwargs):
        raise OSError(f"cannot close {settings.database_url} credential=SECRETMARKER")

    monkeypatch.setattr(type(engine), "dispose", failing_dispose)
    with pytest.raises(DeploymentCompositionError) as info:
        asyncio.run(composition.close())
    assert str(info.value) == "Product resources could not be released"
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert "SECRETMARKER" not in repr(info.value) + caplog.text


# ----- deployment factory ------------------------------------------------------------


def product_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}


@pytest.mark.parametrize("environment", ["local", "test"])
def test_disabled_deployment_boots_with_503_business_routes(settings, runtime_settings,
                                                            environment) -> None:  # fmt: skip
    store = "0b0b0b0b-0000-4000-8000-000000000001"
    granted = principal(store_ids=frozenset({store}),
                        permissions=frozenset({"orders.read", "tickets.create"}))  # fmt: skip
    s = deployment_settings(settings, environment, product_api_keys=(granted,))
    assert s.business_backend == "disabled"
    with TestClient(create_deployment_app(s, runtime_settings)) as client:
        assert client.get("/health").status_code == 200
        headers = product_headers()
        run = {"message": "hi", "store_id": store}
        assert client.post("/api/v1/operations/runs", json=run).status_code == 401
        assert client.post("/api/v1/operations/runs", json=run,
                           headers=headers).status_code == 503  # fmt: skip
        ticket = {"store_id": store, "title": "t", "description": "d"}
        assert (
            client.post(
                "/api/v1/operations/tickets",
                json=ticket,
                headers=headers | {"Idempotency-Key": "k" * 20},
            ).status_code
            == 503
        )
        status = "/api/v1/operations/tickets/commands"
        params = {"command_id": "0c0c0c0c-0000-4000-8000-000000000001"}
        assert client.get(status, params=params, headers=headers).status_code == 503
        report = client.get("/api/v1/operations/reports/daily", params={"store_id": store},
                            headers=headers)  # fmt: skip
        assert (report.status_code, report.json()) == (
            503, {"detail": "Daily operations report unavailable"})  # fmt: skip
        assert client.get("/agents").status_code == 401


def test_mock_deployment_builds_and_releases_its_engine_on_shutdown(settings, runtime_settings,
                                                                    built) -> None:  # fmt: skip
    app = create_deployment_app(mock_settings(settings), runtime_settings,
                                model=ScriptedToolModel())  # fmt: skip
    engine = built.obj("create_product_engine")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert built.disposed == []  # alive while the application runs
        agent_ids = {
            a["id"]
            for a in client.get(
                "/agents", headers={"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
            ).json()
        }
        assert "operations" not in agent_ids  # never registered with AgentOS
    assert built.disposed == [engine.sync_engine]  # exactly once, at shutdown


def test_bootstrap_takes_no_auth_or_service_overrides() -> None:
    import inspect

    params = inspect.signature(create_deployment_app).parameters
    assert list(params) == ["settings", "runtime_settings", "model"]
    assert params["model"].kind is inspect.Parameter.KEYWORD_ONLY


# ----- low-level factory -------------------------------------------------------------


def test_create_app_shutdown_callback_runs_once_and_is_optional(settings, runtime_settings):
    from app.main import create_app

    calls: list[str] = []

    async def callback() -> None:
        calls.append("closed")

    with TestClient(create_app(settings, runtime_settings, shutdown_callback=callback)) as c:
        assert c.get("/health").status_code == 200
        assert calls == []
    assert calls == ["closed"]
    with TestClient(create_app(settings, runtime_settings)) as c:  # unchanged without it
        assert c.get("/health").status_code == 200


def test_create_app_ignores_the_business_backend(settings, runtime_settings, caplog) -> None:
    """The low-level factory applies no deployment policy (it is injection-oriented)."""
    from app.main import create_app

    caplog.set_level(logging.DEBUG)
    s = deployment_settings(settings, "production", business_backend="mock")
    with TestClient(create_app(s, runtime_settings)) as client:
        assert client.get("/health").status_code == 200
