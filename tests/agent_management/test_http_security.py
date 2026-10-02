"""Agent-management HTTP API and the Operations run gate: Product authentication,
agents.read / agents.manage, audit through the existing coordinator, Agent actors refused,
fail-closed gating of POST /api/v1/operations/runs, OpenAPI. Real routes, service, gate,
coordinator and handlers; in-memory configuration repository and audit sink (PostgreSQL is
proven in tests/integration/test_agent_management_postgres.py). No network, no model.
"""

import asyncio
import socket
from datetime import UTC, datetime

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.agent_management.service import AgentAccessDeniedError
from app.context.models import ActorContext, RequestContext
from app.execution import AuditEventType
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.agent_fakes import RecordingOperationsService, build_agent_service
from tests.support.product_auth import deployment_settings, principal

STORE = "00000000-0000-4000-8000-0000000000aa"
READER = "test-agents-reader-key-" + "r" * 24
MANAGER = "test-agents-manager-key-" + "m" * 24
OPERATOR = "test-agents-operator-key-" + "o" * 24  # runs Operations, cannot manage Agents
CATALOG, AGENTS, AGENT = "/api/v1/agents/catalog", "/api/v1/agents", "/api/v1/agents/agent"
ENABLE, DISABLE = "/api/v1/agents/agent/enable", "/api/v1/agents/agent/disable"
RESET = "/api/v1/agents/agent/configuration"
RUNS = "/api/v1/operations/runs"
OPS = {"agent_id": "operations"}


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


class World:
    def __init__(self, settings, *, with_agents: bool = True, with_operations: bool = True):
        self.service, self.repository, self.audit = build_agent_service()
        self.operations = RecordingOperationsService()
        keys = (
            principal(READER, key_id="reader", actor_id="reader",
                      permissions=frozenset({"agents.read"})),
            principal(MANAGER, key_id="manager", actor_id="manager",
                      permissions=frozenset({"agents.read", "agents.manage", "orders.read"}),
                      store_ids=frozenset({STORE})),
            principal(OPERATOR, key_id="operator", actor_id="operator",
                      permissions=frozenset({"orders.read"}), store_ids=frozenset({STORE})),
        )  # fmt: skip
        configured = deployment_settings(settings, "test", product_api_keys=keys)
        self.company = configured.company_id
        self.app = create_app(
            configured, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY),
            operations_service=self.operations if with_operations else None,
            agent_service=self.service if with_agents else None,
        )  # fmt: skip

    def run(self, client: TestClient, key: str = OPERATOR):
        return client.post(RUNS, headers=auth(key), json={"message": "hello", "store_id": STORE})


@pytest.fixture
def world(settings) -> World:
    return World(settings)


# ----- authentication and permissions ------------------------------------------------------------


def test_unauthenticated_requests_are_refused(world: World) -> None:
    with TestClient(world.app) as client:
        for method, path in (("GET", CATALOG), ("GET", AGENTS), ("GET", AGENT),
                             ("POST", ENABLE), ("POST", DISABLE), ("DELETE", RESET)):  # fmt: skip
            assert client.request(method, path, params=OPS).status_code == 401, path
            wrong = {"Authorization": "Bearer test-wrong-" + "x" * 40}
            assert client.request(method, path, params=OPS, headers=wrong).status_code == 401
            agentos = {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
            assert client.request(method, path, params=OPS, headers=agentos).status_code == 401
    assert world.audit.events == [] and world.repository.rows == {}


def test_reads_need_agents_read(world: World) -> None:
    with TestClient(world.app) as client:
        for path in (CATALOG, AGENTS, AGENT):
            denied = client.get(path, params=OPS, headers=auth(OPERATOR))
            assert denied.status_code == 403 and denied.json() == {"detail": "Forbidden"}
            assert client.get(path, params=OPS, headers=auth(READER)).status_code == 200


def test_mutations_need_agents_manage_and_denials_are_audited(world: World) -> None:
    with TestClient(world.app) as client:
        for key in (READER, OPERATOR):
            for method, path in (("POST", ENABLE), ("POST", DISABLE), ("DELETE", RESET)):
                response = client.request(method, path, params=OPS, headers=auth(key))
                assert response.status_code == 403, (key, path)
    assert world.repository.rows == {}
    denied = [e for e in world.audit.events if e.event_type is AuditEventType.POLICY_DECIDED]
    assert len(denied) == 6 and all(e.policy_outcome.value == "deny" for e in denied)  # fmt: skip
    assert {e.actor_id for e in denied} == {"reader", "operator"}


def test_catalog_and_effective_state(world: World) -> None:
    with TestClient(world.app) as client:
        catalog = client.get(CATALOG, headers=auth(READER)).json()["agents"]
        assert [a["agent_id"] for a in catalog] == ["operations"]
        manifest = catalog[0]["manifest"]
        assert manifest["tool_call_limit"] == 6
        assert [t["tool_id"] for t in manifest["tools"]] == [
            "get_order", "get_order_shipments", "get_daily_operations_report",
            "create_operational_ticket"]  # fmt: skip
        listed = client.get(AGENTS, headers=auth(READER)).json()["agents"]
        assert [a["definition"]["agent_id"] for a in listed] == ["operations"]
        assert listed[0]["state"] == {"enabled": True, "source": "default",
                                      "availability": "available", "reason": None,
                                      "updated_at": None}  # fmt: skip
        text = client.get(CATALOG, headers=auth(READER)).text.lower()
    for leaked in ("instruction", "you are an operations agent", "app.agents", "import",
                   "generic-reasoning", "api_key", "shopify", "f" + "ulfly"):  # fmt: skip
        assert leaked not in text, leaked


def test_unknown_and_malformed_agent_ids_fail_safely(world: World) -> None:
    with TestClient(world.app) as client:
        for path, method in (
            (AGENT, "GET"),
            (ENABLE, "POST"),
            (DISABLE, "POST"),
            (RESET, "DELETE"),
        ):
            missing = client.request(method, path, params={"agent_id": "finance"},
                                     headers=auth(MANAGER))  # fmt: skip
            assert missing.status_code == 404
            assert missing.json() == {"detail": "Agent not found"}
            for bad in ("generic-reasoning", "app.agents.operations:Agent", "../x", "Operations"):
                response = client.request(method, path, params={"agent_id": bad},
                                          headers=auth(MANAGER))  # fmt: skip
                assert response.status_code in (404, 422), bad
                assert bad not in response.text
            assert client.request(method, path, headers=auth(MANAGER)).status_code == 422
    assert world.repository.rows == {}


# ----- enable / disable / reset and the Operations run gate ---------------------------------------


def test_disable_blocks_operations_runs_and_enable_restores_them(world: World) -> None:
    with TestClient(world.app) as client:
        assert world.run(client).status_code == 200 and len(world.operations.calls) == 1

        disabled = client.post(DISABLE, params=OPS, headers=auth(MANAGER))
        assert disabled.status_code == 200
        assert disabled.json()["agent"]["state"]["availability"] == "disabled"
        assert disabled.json()["agent"]["state"]["source"] == "override"

        refused = world.run(client)
        assert refused.status_code == 409
        assert refused.json() == {"detail": "Operations Agent is disabled"}
        assert len(world.operations.calls) == 1  # the runner (model, tools) never ran

        enabled = client.post(ENABLE, params=OPS, headers=auth(MANAGER))
        assert enabled.json()["agent"]["state"]["availability"] == "available"
        assert world.run(client).status_code == 200 and len(world.operations.calls) == 2

        client.post(DISABLE, params=OPS, headers=auth(MANAGER))
        reset = client.delete(RESET, params=OPS, headers=auth(MANAGER))
        assert reset.status_code == 200
        assert reset.json()["agent"]["state"]["source"] == "default"
        assert world.run(client).status_code == 200 and len(world.operations.calls) == 3

    verified = [(e.action_name, e.verification_code) for e in world.audit.events
                if e.event_type is AuditEventType.VERIFIED]  # fmt: skip
    assert verified == [
        ("agents.agent.disable", "agent_disabled"),
        ("agents.agent.enable", "agent_enabled"),
        ("agents.agent.disable", "agent_disabled"),
        ("agents.configuration.reset", "configuration_reset"),
    ]
    assert {e.actor_id for e in world.audit.events} == {"manager"}
    assert all(e.company_id == world.company and e.store_id is None for e in world.audit.events)


def test_disabled_state_is_per_company(world: World) -> None:
    asyncio.run(world.repository.set_enabled("another-company", "operations", False,
                                             datetime.now(UTC)))  # fmt: skip
    with TestClient(world.app) as client:
        assert world.run(client).status_code == 200


def test_unreadable_configuration_fails_closed(world: World) -> None:
    world.repository.fail = True
    with TestClient(world.app) as client:
        refused = world.run(client)
        assert refused.status_code == 503
        assert refused.json() == {"detail": "Operations service unavailable"}
        assert client.get(AGENTS, headers=auth(READER)).status_code == 503
    assert world.operations.calls == []


def test_without_agent_management_operations_keeps_its_default(settings) -> None:
    world = World(settings, with_agents=False)
    with TestClient(world.app) as client:
        assert world.run(client).status_code == 200
        assert client.get(AGENTS, headers=auth(READER)).status_code == 503


def test_operations_runtime_not_composed_is_reported_unavailable(settings) -> None:
    world = World(settings, with_operations=False)
    with TestClient(world.app) as client:
        state = client.get(AGENT, params=OPS, headers=auth(READER)).json()["agent"]["state"]
        assert state["enabled"] is True and state["availability"] == "unavailable"
        assert state["reason"] == "runtime_not_composed"
        assert world.run(client).status_code == 503


def test_daily_report_route_does_not_depend_on_the_agent_gate() -> None:
    from pathlib import Path

    import app

    root = Path(app.__file__).parent
    for name in ("routes/operations_reports.py", "routes/operations_tickets.py",
                 "workflows/operations_daily.py"):  # fmt: skip
        source = (root / name).read_text()
        assert "agent_management" not in source and "OperationsAgentDisabled" not in source


# ----- Agents never manage Agents ---------------------------------------------------------------


def test_an_agent_actor_is_refused_even_with_the_permission() -> None:
    service, repository, audit = build_agent_service()
    agent = ActorContext(
        actor_id="operations-agent", actor_type="system_agent", company_id="c",
        permissions=frozenset({"agents.read", "agents.manage"}),
    )  # fmt: skip
    context = RequestContext(actor=agent)
    for call in (lambda: service.set_enabled(context, "operations", False),
                 lambda: service.set_enabled(context, "operations", True),
                 lambda: service.reset_configuration(context, "operations"),
                 lambda: service.list_agents(context)):  # fmt: skip
        with pytest.raises(AgentAccessDeniedError):
            asyncio.run(call())
    with pytest.raises(AgentAccessDeniedError):
        service.catalog(context)
    assert repository.rows == {}
    decided = [e for e in audit.events if e.event_type is AuditEventType.POLICY_DECIDED]
    assert [e.policy_outcome.value for e in decided] == ["deny"] * 3
    assert {e.actor_type for e in decided} == {"system_agent"}


# ----- OpenAPI ----------------------------------------------------------------------------------


def test_openapi_documents_the_agent_routes(world: World) -> None:
    with TestClient(world.app) as client:
        schema = client.get("/openapi.json").json()
    paths = {p: schema["paths"][p] for p in schema["paths"] if p.startswith("/api/v1/agents")}
    assert set(paths) == {CATALOG, AGENTS, AGENT, ENABLE, DISABLE, RESET}
    for path, operations in paths.items():
        for method, operation in operations.items():
            assert operation["tags"] == ["agents"], (path, method)
            assert operation["summary"]
            needed = "agents.manage" if method in ("post", "delete") else "agents.read"
            assert f"`{needed}`" in operation["description"], (path, method)
    names = set(schema["components"]["schemas"])
    assert {"AgentCatalogResponse", "AgentListResponse", "ProductAgentResponse",
            "AgentManifestView", "AgentStateView"} <= names  # fmt: skip
    state = schema["components"]["schemas"]["AgentStateView"]["properties"]
    assert set(state) == {"enabled", "source", "availability", "reason", "updated_at"}
