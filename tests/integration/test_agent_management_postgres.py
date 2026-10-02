"""Task 032 on the REAL mock deployment (``create_deployment_app``) and migrated
PostgreSQL: disabling the Operations Agent through the Product API stops the run before
the model or any tool is reached; re-enabling restores it; the override is durable
across a new application, audited by the existing audit trail, and reset restores the
Product default. The only override is the deterministic scripted model (no LLM call).
"""

import socket
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.integrations.commerce.mock import EntityType, canonical_id
from app.persistence import PostgresAgentConfigurationRepository, create_product_engine
from app.persistence.database import create_session_factory
from tests.integration.product_db import audit_rows
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
STORE = str(canonical_id(EntityType.STORE, "shop_south"))
ORDER = str(canonical_id(EntityType.ORDER, "ord_2002"))
HEADERS = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
RUNS = "/api/v1/operations/runs"
OPS = {"agent_id": "operations"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket.connect

    def local_only(sock: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            raise AssertionError("unexpected outbound connection")
        return real(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture
def clean(migrated: str, engine: sa.Engine):
    """The mock company is shared with other tests: never leave an override behind."""

    def wipe() -> None:
        with engine.begin() as connection:
            connection.execute(sa.text(
                "DELETE FROM product.agent_configurations WHERE company_id = :c"),
                {"c": COMPANY})  # fmt: skip

    wipe()
    yield
    wipe()


def app_for(settings, runtime_settings, model: ScriptedToolModel):
    grant = principal(
        key_id="agents-e2e", actor_id="agents-e2e-admin",
        permissions=frozenset({"orders.read", "shipments.read", "agents.read", "agents.manage"}),
        store_ids=frozenset({STORE}),
    )  # fmt: skip
    configured = deployment_settings(
        settings, "test", company_id=COMPANY, product_api_keys=(grant,), business_backend="mock"
    )
    return create_deployment_app(configured, runtime_settings, model=model)


def script() -> ScriptedToolModel:
    return ScriptedToolModel(script=[CallTool("get_order", {"order_id": ORDER}),
                                     Reply("Order analyzed.")])  # fmt: skip


def run(client: TestClient):
    return client.post(RUNS, headers=HEADERS,
                       json={"message": f"Analyze order {ORDER}.", "store_id": STORE})  # fmt: skip


def stored(engine: sa.Engine) -> list[dict]:
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(
            "SELECT company_id, agent_id, enabled FROM product.agent_configurations "
            "WHERE company_id = :c"), {"c": COMPANY}).mappings()]  # fmt: skip


def test_disable_stops_model_and_tools_enable_restores_reset_defaults(
    settings, runtime_settings, engine: sa.Engine, clean
) -> None:
    model = script()
    with TestClient(app_for(settings, runtime_settings, model)) as client:
        assert client.get("/api/v1/agents", headers=HEADERS).json()["agents"][0]["state"][
            "availability"] == "available"  # fmt: skip
        assert client.post("/api/v1/agents/agent/disable", params=OPS,
                           headers=HEADERS).status_code == 200  # fmt: skip
        refused = run(client)
        assert refused.status_code == 409
        assert refused.json() == {"detail": "Operations Agent is disabled"}
        assert model.requests == []  # the model was never called, so no tool could run
        assert model.tool_results() == {}

    # Durable: a brand-new application on the same database is still disabled.
    model = script()
    with TestClient(app_for(settings, runtime_settings, model)) as client:
        assert run(client).status_code == 409 and model.requests == []
        assert client.post("/api/v1/agents/agent/enable", params=OPS,
                           headers=HEADERS).status_code == 200  # fmt: skip
        ran = run(client)
        assert ran.status_code == 200 and ran.json()["message"] == "Order analyzed."
        assert len(model.requests) == 2  # model -> get_order tool -> model
        assert model.tool_results()["get_order"][0]["outcome"] == "ok"
        assert stored(engine) == [{"company_id": COMPANY, "agent_id": "operations",
                                   "enabled": True}]  # fmt: skip
        reset = client.delete("/api/v1/agents/agent/configuration", params=OPS, headers=HEADERS)
        assert reset.json()["agent"]["state"]["source"] == "default"
    assert stored(engine) == []

    verified = [(r["action_name"], r["verification_code"])
                for r in audit_rows(engine, company_id=COMPANY, event_type="verified")
                if r["action_name"].startswith("agents.")]  # fmt: skip
    assert verified[-3:] == [("agents.agent.disable", "agent_disabled"),
                             ("agents.agent.enable", "agent_enabled"),
                             ("agents.configuration.reset", "configuration_reset")]  # fmt: skip
    # No Operations write happened while disabled (the only writes were Agent management).
    assert not [r for r in audit_rows(engine, company_id=COMPANY, actor_id="agents-e2e-admin")
                if not r["action_name"].startswith("agents.")]  # fmt: skip


def test_repository_upsert_is_scoped_and_keeps_created_at(
    settings, engine: sa.Engine, clean
) -> None:
    import asyncio

    async def scenario() -> None:
        product_engine = create_product_engine(str(settings.database_url))
        try:
            repo = PostgresAgentConfigurationRepository(create_session_factory(product_engine))
            first = datetime(2031, 1, 1, tzinfo=UTC)
            created = await repo.set_enabled(COMPANY, "operations", False, first)
            updated = await repo.set_enabled(COMPANY, "operations", True,
                                             first + timedelta(hours=1))  # fmt: skip
            assert created.created_at == updated.created_at == first
            assert updated.updated_at == first + timedelta(hours=1) and updated.enabled
            assert await repo.get("another-company", "operations") is None
            assert [c.agent_id for c in await repo.list(COMPANY)] == ["operations"]
            assert await repo.delete(COMPANY, "operations") is True
            assert await repo.delete(COMPANY, "operations") is False
            assert await repo.get(COMPANY, "operations") is None
        finally:
            await product_engine.dispose()

    asyncio.run(scenario())


def test_stored_values_cannot_select_code(engine: sa.Engine, clean) -> None:
    with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(sa.text(
            "INSERT INTO product.agent_configurations VALUES "
            "(:c, 'app.agents.operations:Agent', true, now(), now())"), {"c": COMPANY})  # fmt: skip
    with engine.connect() as connection:
        columns = {r[0] for r in connection.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'product' AND table_name = 'agent_configurations'"))}  # fmt: skip
    assert columns == {"company_id", "agent_id", "enabled", "created_at", "updated_at"}
