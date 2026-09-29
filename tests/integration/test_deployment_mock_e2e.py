"""The LOCAL/TEST mock deployment, end to end over HTTP against real PostgreSQL.

REAL, all composed by ``create_deployment_app`` (nothing injected but the model):
FastAPI + AgentOS, Product API-key authentication, the Product routes and services,
OperationsAgentRunner, GovernanceGate, ExecutionCoordinator, WriteCommandCoordinator,
PostgresWriteCommandStore and PostgresAuditSink (PostgreSQL, migrated to 0002), and
the deterministic MockCommerceAdapter / MockTicketingAdapter on one MockCommerceSystem.

The only explicit override is the deterministic scripted model (no LLM call). The
mock ticket desk and the Product engine are OBSERVED through wrappers around the names
the local mock composition builds with; the real objects are used unchanged.

The mock ticket desk is in memory: it does not survive a restart, and nothing here
claims it does. Restart durability is proven for Product-owned PostgreSQL state only.
"""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.composition import local_mock
from app.integrations.commerce import ShipmentQuery
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter, canonical_id
from tests.integration.product_db import audit_rows, rows_for_key
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))  # the mock account
STORE = str(canonical_id(EntityType.STORE, "shop_south"))
OTHER_STORE = str(canonical_id(EntityType.STORE, "shop_north"))  # same company, not granted
ORDER = str(canonical_id(EntityType.ORDER, "ord_2002"))  # a failed + a moving shipment
PROVIDER_KEYS = ("acct_demo", "shop_south", "ord_2002", "ship_507", "ship_509", "cus_005",
                 "tkt_")  # fmt: skip
RUNS, TICKETS = "/api/v1/operations/runs", "/api/v1/operations/tickets"
STATUS = "/api/v1/operations/tickets/commands"
HEADERS = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
TITLE, DESCRIPTION = "Failed delivery", "Shipment needs operations follow-up."
NOT_REQUESTED = {"status": "denied", "reason": "action_not_requested", "ticket_id": None}
LIFECYCLE = ["requested", "policy_decided", "execution_started", "execution_completed",
             "verification_started", "verified"]  # fmt: skip


class Observed:
    def __init__(self) -> None:
        self.desks: list = []
        self.engines: list = []
        self.disposed: list = []
        self.systems: list = []


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> Observed:
    seen = Observed()
    original_desk = local_mock.MockTicketDesk
    original_engine = local_mock.create_product_engine
    original_system = local_mock.MockCommerceSystem

    def desk(*args, **kwargs):
        seen.desks.append(original_desk(*args, **kwargs))
        return seen.desks[-1]

    def system(*args, **kwargs):
        seen.systems.append(original_system(*args, **kwargs))
        return seen.systems[-1]

    def engine(*args, **kwargs):
        created = original_engine(*args, **kwargs)
        sa.event.listen(created.sync_engine, "engine_disposed", seen.disposed.append)
        seen.engines.append(created)
        return created

    monkeypatch.setattr(local_mock, "MockTicketDesk", desk)
    monkeypatch.setattr(local_mock, "MockCommerceSystem", system)
    monkeypatch.setattr(local_mock, "create_product_engine", engine)
    return seen


def mock_deployment(settings):
    grant = principal(
        key_id="deploy-e2e", actor_id="deploy-ops-actor",
        permissions=frozenset({"orders.read", "shipments.read", "tickets.create"}),
        store_ids=frozenset({STORE}),
    )  # fmt: skip
    return deployment_settings(settings, "test", company_id=COMPANY, product_api_keys=(grant,),
                               business_backend="mock")  # fmt: skip


def company_counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        commands = connection.execute(sa.text(
            "SELECT count(*) FROM product.write_commands WHERE company_id = :c"), {"c": COMPANY}
        ).scalar_one()  # fmt: skip
        audits = connection.execute(sa.text(
            "SELECT count(*) FROM product.audit_events WHERE company_id = :c"), {"c": COMPANY}
        ).scalar_one()  # fmt: skip
    return commands, audits


def expected_analysis() -> str:
    """Computed independently from a fresh deterministic mock adapter."""

    async def read():
        adapter = MockCommerceAdapter()
        order = await adapter.get_order(UUID(ORDER))
        shipments = await adapter.list_shipments(ShipmentQuery(order_id=UUID(ORDER)))
        return order.status.value, sorted(s.status.value for s in shipments)

    status, shipments = asyncio.run(read())
    return f"Order is {status}; shipments: {', '.join(shipments)}."


def analysis(results: dict) -> str:
    order = results["get_order"][-1]
    shipments = results["get_order_shipments"][-1]
    if order["outcome"] != "ok" or shipments["outcome"] != "ok":
        return f"Could not analyze: order {order['outcome']}, shipments {shipments['outcome']}."
    statuses = sorted(s["status"] for s in shipments["shipments"])
    return f"Order is {order['order']['status']}; shipments: {', '.join(statuses)}."


def ticket_body() -> dict:
    return {"store_id": STORE, "title": TITLE, "description": DESCRIPTION}


# ----- POST /api/v1/operations/runs ---------------------------------------------------


def test_operations_run_reads_real_mock_data_read_only(settings, runtime_settings, migrated,
                                                       engine, observed) -> None:  # fmt: skip
    model = ScriptedToolModel(script=[
        CallTool("get_order", {"order_id": ORDER}),
        CallTool("get_order_shipments", {"order_id": ORDER}),
        Reply(analysis),
    ])  # fmt: skip
    before = company_counts(engine)
    app = create_deployment_app(mock_deployment(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        # Product auth: no key / wrong key never reach the service or the model.
        body = {"message": f"Analyze order {ORDER} and its shipments.", "store_id": STORE}
        assert client.post(RUNS, json=body).status_code == 401
        wrong = {"Authorization": "Bearer test-wrong-key-" + "z" * 32}
        assert client.post(RUNS, json=body, headers=wrong).status_code == 401
        # Exact store scope: another store of the same company is forbidden.
        assert client.post(RUNS, json=body | {"store_id": OTHER_STORE},
                           headers=HEADERS).status_code == 403  # fmt: skip
        assert model.requests == []

        response = client.post(RUNS, json=body, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == response.headers["X-Request-ID"]
    assert data["message"] == expected_analysis()  # real mock order + shipment data
    # Canonical statuses, never the provider's ("delivery_failed", "moving").
    assert data["message"] == "Order is processing; shipments: failed, in_transit."
    assert "delivery_failed" not in response.text

    results = model.tool_results()
    (order,) = results["get_order"]
    (shipments,) = results["get_order_shipments"]
    assert order["outcome"] == "ok" and order["order"]["order_id"] == ORDER
    assert order["order"]["currency"] == "EUR"  # the granted store's (shop_south) data
    assert shipments["outcome"] == "ok" and len(shipments["shipments"]) == 2
    assert len(observed.systems) == 1  # the one composed mock system served the reads

    # Read-only: no command, no audit lifecycle, no ticket.
    assert company_counts(engine) == before
    (desk,) = observed.desks
    assert desk.ticket_count == 0
    for key in PROVIDER_KEYS:
        assert key not in response.text
        assert key not in model.visible_text()


def test_malicious_model_cannot_create_a_ticket_through_runs(settings, runtime_settings,
                                                             migrated, engine,
                                                             observed) -> None:  # fmt: skip
    ticket = CallTool("create_operational_ticket", {"title": TITLE, "description": DESCRIPTION})
    model = ScriptedToolModel(script=[
        CallTool("get_order", {"order_id": ORDER}), ticket, ticket,
        Reply(lambda r: json.dumps(r["create_operational_ticket"])),
    ])  # fmt: skip
    before = company_counts(engine)
    app = create_deployment_app(mock_deployment(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        response = client.post(RUNS, headers=HEADERS, json={
            "message": "Create a ticket for this order now.", "store_id": STORE})  # fmt: skip
    assert response.status_code == 200
    assert model.tool_results()["create_operational_ticket"] == [NOT_REQUESTED] * 2
    assert company_counts(engine) == before  # no WriteCommand, no audit lifecycle
    (desk,) = observed.desks
    assert desk.ticket_count == 0  # no external ticket


# ----- ticket write, replay, status ---------------------------------------------------


def test_ticket_write_replay_and_status(settings, runtime_settings, migrated, engine,
                                        observed) -> None:  # fmt: skip
    key = f"deploy-e2e-{uuid4()}"
    headers = HEADERS | {"Idempotency-Key": key}
    app = create_deployment_app(mock_deployment(settings), runtime_settings,
                                model=ScriptedToolModel())  # fmt: skip
    with TestClient(app) as client:
        first = client.post(TICKETS, json=ticket_body(), headers=headers)
        assert first.status_code == 201
        data = first.json()
        assert (data["status"], data["reason"], data["replayed"]) == ("verified", "verified",
                                                                       False)  # fmt: skip
        ticket_id = UUID(data["ticket_id"])
        (desk,) = observed.desks
        assert desk.ticket_count == 1

        (command,) = rows_for_key(engine, key)
        assert command["status"] == "verified" and str(command["command_id"]) == data["command_id"]
        assert command["execution_reference_id"] == str(ticket_id)
        assert (command["company_id"], command["store_id"], command["actor_id"]) == (
            COMPANY, STORE, "deploy-ops-actor",
        )  # fmt: skip
        lifecycle = audit_rows(engine, run_id=command["action_run_id"])
        assert [r["event_type"] for r in lifecycle] == LIFECYCLE
        # Verified by the independent correlation re-read.
        assert lifecycle[-1]["verification_code"] == "ticket_present"
        assert lifecycle[-1]["execution_reference_id"] == str(ticket_id)
        counts = company_counts(engine)

        # Exact replay: same command and ticket, nothing executed or audited again.
        replay = client.post(TICKETS, json=ticket_body(), headers=headers)
        assert replay.status_code == 200
        again = replay.json()
        assert again["replayed"] is True and again["status"] == "verified"
        assert (again["command_id"], again["ticket_id"]) == (data["command_id"], data["ticket_id"])
        assert desk.ticket_count == 1
        assert company_counts(engine) == counts
        assert audit_rows(engine, run_id=command["action_run_id"]) == lifecycle

        # Status: durable VERIFIED state; the read executes and audits nothing.
        status = client.get(STATUS, params={"command_id": data["command_id"]}, headers=HEADERS)
        assert status.status_code == 200
        state = status.json()
        assert (state["command_id"], state["status"], state["ticket_id"]) == (
            data["command_id"], "verified", data["ticket_id"],
        )  # fmt: skip
        assert desk.ticket_count == 1
        assert company_counts(engine) == counts

    for text in (first.text, replay.text, status.text):
        assert key not in text
        for provider_key in PROVIDER_KEYS:
            assert provider_key not in text


def test_command_status_survives_an_application_restart(settings, runtime_settings, migrated,
                                                        engine, observed) -> None:  # fmt: skip
    key = f"deploy-restart-{uuid4()}"
    s = mock_deployment(settings)
    app_a = create_deployment_app(s, runtime_settings, model=ScriptedToolModel())
    with TestClient(app_a) as client:
        created = client.post(TICKETS, json=ticket_body(), headers=HEADERS | {
            "Idempotency-Key": key})  # fmt: skip
    assert created.status_code == 201
    data = created.json()
    engine_a = observed.engines[0]
    assert observed.disposed == [engine_a.sync_engine]  # app A is gone, engine released

    # A NEW application: new engine, new (empty, in-memory) mock desk, same database.
    app_b = create_deployment_app(s, runtime_settings, model=ScriptedToolModel())
    with TestClient(app_b) as client:
        status = client.get(STATUS, params={"command_id": data["command_id"]}, headers=HEADERS)
    assert status.status_code == 200
    state = status.json()
    assert (state["command_id"], state["status"], state["ticket_id"]) == (
        data["command_id"], "verified", data["ticket_id"],
    )  # fmt: skip
    desk_a, desk_b = observed.desks
    assert desk_a.ticket_count == 1
    # The answer came from PostgreSQL, not from the provider: B's desk is empty.
    assert desk_b.ticket_count == 0
    assert len(observed.engines) == 2 and observed.engines[1] is not engine_a
    assert observed.disposed == [engine_a.sync_engine, observed.engines[1].sync_engine]


def test_deployment_keeps_product_and_agentos_credentials_separate(
    settings, runtime_settings, migrated
) -> None:
    from tests.conftest import TEST_OS_SECURITY_KEY

    app = create_deployment_app(mock_deployment(settings), runtime_settings,
                                model=ScriptedToolModel())  # fmt: skip
    os_headers = {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
    params = {"command_id": str(uuid4())}
    with TestClient(app) as client:
        assert client.get("/agents", headers=HEADERS).status_code == 401
        agents = client.get("/agents", headers=os_headers)
        assert agents.status_code == 200
        assert "operations" not in {a["id"] for a in agents.json()}
        assert client.get(STATUS, params=params, headers=os_headers).status_code == 401
        assert client.get(STATUS, params=params, headers=HEADERS).status_code == 404
