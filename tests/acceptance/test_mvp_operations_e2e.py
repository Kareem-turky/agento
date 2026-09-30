"""MVP BUSINESS ACCEPTANCE (Task 029): the first MVP use case, end to end.

REAL: everything composed by ``create_deployment_app`` in the ``test`` environment with
the ``mock`` business backend: FastAPI + AgentOS, Product API-key authentication, the
Product HTTP routes and services, GovernanceGate, ExecutionCoordinator,
WriteCommandCoordinator, PostgresWriteCommandStore + PostgresAuditSink on a real,
migrated PostgreSQL, the deterministic DailyOperationsWorkflow and the Operations Agent.

TEST-ONLY: the deterministic mock commerce/ticketing fixture (unchanged) and the
``ScriptedToolModel`` passed through the existing ``model=`` test seam. No runtime model
provider, no live LLM, no provider API, no external HTTP: a guard fails the test on any
Python-level outbound connection. Every business result is obtained through Product
HTTP; the database is only inspected to prove durability and audit.

This is NOT the packaged-installation acceptance (the four-service deployment smoke in
the Infrastructure CI job is), and it never claims production readiness: production
stays fail-closed until a reviewed real business backend exists.
"""

import json
import socket
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.composition import local_mock
from tests.integration.product_db import audit_rows, rows_for_key
from tests.support.canonical_mock import (
    AGENT_NEVER_SHOWS,
    BUSINESS_DATE,
    COMPANY,
    EXPECTED_AFFECTED_ORDERS,
    EXPECTED_COVERAGE,
    EXPECTED_FAILED_SHIPMENTS,
    EXPECTED_FINDINGS,
    EXPECTED_ORDERS_CREATED,
    EXPECTED_SHIPMENTS_SHIPPED,
    EXPECTED_TIMEZONE,
    EXPECTED_WINDOW,
    NORTH,
    REPORT_NEVER_CONTAINS,
    SOUTH,
)
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import CallTool, Reply, ScriptedToolModel

pytestmark = pytest.mark.integration

HEALTH, RUNS = "/health", "/api/v1/operations/runs"
REPORT, TICKETS = "/api/v1/operations/reports/daily", "/api/v1/operations/tickets"
COMMANDS = "/api/v1/operations/tickets/commands"
REPORT_TOOL, TICKET_TOOL = "get_daily_operations_report", "create_operational_ticket"


class Credentials(dict[str, str]):
    """Request headers whose repr never shows a key: pytest's failure explanation prints
    call arguments, and a Product API key or Authorization header must never be dumped."""

    def __repr__(self) -> str:
        return "<redacted credentials>"

    def __or__(self, other: dict[str, str]) -> "Credentials":  # type: ignore[override]
        return Credentials({**self, **other})


def bearer(key: str) -> Credentials:
    return Credentials({"Authorization": f"Bearer {key}"})


# A second, obviously test-only principal of the same company, granted ONLY the other store.
NORTH_KEY = "test-product-key-acceptance-north-" + "n" * 24  # noqa: S105 - test-only
OPERATOR, NORTH_OPERATOR = bearer(TEST_PRODUCT_KEY), bearer(NORTH_KEY)
NO_CREDENTIALS = Credentials()
TITLE = "Failed shipment follow-up"
DESCRIPTION = "The daily report shows a failed shipment; operations must review it."
NOT_REQUESTED = {"status": "denied", "reason": "action_not_requested", "ticket_id": None}
LIFECYCLE = ["requested", "policy_decided", "execution_started", "execution_completed",
             "verification_started", "verified"]  # fmt: skip


# ----- the acceptance harness --------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Any Python-level outbound connection (LLM, provider, arbitrary HTTP) fails the test.
    PostgreSQL is reached through libpq, below this layer, and stays available."""
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"unexpected outbound connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


class Observed:
    """The REAL mock desks and workflows the deployment built (observed, not replaced)."""

    def __init__(self) -> None:
        self.desks: list[Any] = []
        self.workflows: list[Any] = []
        self.agent_kwargs: list[dict[str, Any]] = []


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> Observed:
    seen = Observed()
    original_desk = local_mock.MockTicketDesk
    original_workflow = local_mock.DailyOperationsWorkflow
    original_agent = local_mock.build_operations_agent

    def desk(*args: Any, **kwargs: Any) -> Any:
        seen.desks.append(original_desk(*args, **kwargs))
        return seen.desks[-1]

    def workflow(*args: Any, **kwargs: Any) -> Any:
        seen.workflows.append(original_workflow(*args, **kwargs))
        return seen.workflows[-1]

    def agent(*args: Any, **kwargs: Any) -> Any:
        seen.agent_kwargs.append(kwargs)
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(local_mock, "MockTicketDesk", desk)
    monkeypatch.setattr(local_mock, "DailyOperationsWorkflow", workflow)
    monkeypatch.setattr(local_mock, "build_operations_agent", agent)
    return seen


def acceptance_settings(settings):
    """test environment + mock backend + real Product API-key authentication."""
    operator = principal(
        key_id="mvp-operator", actor_id="mvp-operator-actor",
        permissions=frozenset({"stores.read", "orders.read", "shipments.read", "tickets.create"}),
        store_ids=frozenset({SOUTH}),
    )  # fmt: skip
    north = principal(
        NORTH_KEY, key_id="mvp-north-operator", actor_id="mvp-north-actor",
        permissions=frozenset({"stores.read", "orders.read", "shipments.read", "tickets.create"}),
        store_ids=frozenset({NORTH}),
    )  # fmt: skip
    return deployment_settings(settings, "test", company_id=COMPANY, business_backend="mock",
                               product_api_keys=(operator, north))  # fmt: skip


def durable_counts(engine: sa.Engine) -> tuple[int, int]:
    """(write commands, audit events) of the canonical company, straight from PostgreSQL."""
    with engine.connect() as connection:
        commands = connection.execute(sa.text(
            "SELECT count(*) FROM product.write_commands WHERE company_id = :c"), {"c": COMPANY}
        ).scalar_one()  # fmt: skip
        audits = connection.execute(sa.text(
            "SELECT count(*) FROM product.audit_events WHERE company_id = :c"), {"c": COMPANY}
        ).scalar_one()  # fmt: skip
    return commands, audits


def assert_canonical_report(report: dict[str, Any]) -> None:
    """The pinned deterministic Daily Operations Report of the canonical store and date."""
    assert (report["store_id"], report["business_date"], report["timezone"]) == (
        SOUTH, BUSINESS_DATE, EXPECTED_TIMEZONE,
    )  # fmt: skip
    assert (report["window_start"], report["window_end"]) == EXPECTED_WINDOW
    metrics = report["metrics"]
    assert metrics["orders_created"] == EXPECTED_ORDERS_CREATED
    assert metrics["shipments_shipped"] == EXPECTED_SHIPMENTS_SHIPPED
    assert metrics["affected_orders"] == EXPECTED_AFFECTED_ORDERS
    shipments = {c["status"]: c["count"] for c in metrics["shipment_status_counts"]}
    assert shipments["failed"] == EXPECTED_FAILED_SHIPMENTS
    assert report["findings"] == EXPECTED_FINDINGS
    assert sum(f["severity"] == "critical" and f["entity_type"] == "shipment"
               for f in report["findings"]) == 1  # fmt: skip
    assert (report["findings_total"], report["findings_truncated"]) == (1, False)
    assert report["coverage"] == EXPECTED_COVERAGE  # inventory: not_included


def summary(results: dict[str, list[dict[str, Any]]]) -> str:
    """The scripted model's answer, built ONLY from the report tool result it received."""
    result = results[REPORT_TOOL][-1]
    if result["outcome"] != "ok":
        return "The daily operations report could not be produced."
    report, metrics = result["report"], result["report"]["metrics"]
    failed = {c["status"]: c["count"] for c in metrics["shipment_status_counts"]}["failed"]
    critical = sum(f["severity"] == "critical" for f in report["findings"])
    return (f"Orders created: {metrics['orders_created']}. Shipments shipped: "
            f"{metrics['shipments_shipped']}. Failed shipments: {failed}. Critical shipment "
            f"failures: {critical}. Inventory: {report['coverage']['inventory']}.")  # fmt: skip


def ticket_body() -> dict[str, str]:
    return {"store_id": SOUTH, "title": TITLE, "description": DESCRIPTION}


# ----- acceptance 1: read path ------------------------------------------------------------


def test_operations_analysis_and_daily_report_are_consistent_and_read_only(
    settings, runtime_settings, migrated, engine, observed, no_outbound_network
) -> None:
    model = ScriptedToolModel(script=[
        CallTool(REPORT_TOOL, {"business_date": BUSINESS_DATE}), Reply(summary),
    ])  # fmt: skip
    app = create_deployment_app(acceptance_settings(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        health = client.get(HEALTH)
        assert health.status_code == 200 and health.json()["status"] == "ok"

        direct = client.get(REPORT, headers=OPERATOR,
                            params={"store_id": SOUTH, "business_date": BUSINESS_DATE})  # fmt: skip
        assert direct.status_code == 200
        report = direct.json()["report"]
        assert_canonical_report(report)

        before = durable_counts(engine)
        run = client.post(RUNS, headers=OPERATOR, json={
            "message": f"Analyze operations for {BUSINESS_DATE}.", "store_id": SOUTH})  # fmt: skip
        after = durable_counts(engine)
        (desk,) = observed.desks
        tickets = desk.ticket_count

    # The Agent answered, from exactly one call of the Daily Operations Report tool.
    assert run.status_code == 200
    assert run.json()["request_id"] == run.headers["X-Request-ID"]
    assert run.json()["message"] == (
        "Orders created: 1. Shipments shipped: 1. Failed shipments: 1. Critical shipment "
        "failures: 1. Inventory: not_included."
    )
    results = model.tool_results()
    assert set(results) == {REPORT_TOOL}
    (tool_result,) = results[REPORT_TOOL]
    assert tool_result["outcome"] == "ok"

    # Consistency: the Agent saw the SAME deterministic workflow output as the HTTP
    # report (only the per-request generated_at differs), from ONE workflow instance.
    assert {**tool_result["report"], "generated_at": None} == {**report, "generated_at": None}
    (workflow,) = observed.workflows
    (agent_kwargs,) = observed.agent_kwargs
    assert agent_kwargs["daily_operations"] is workflow

    # No provider/private data through the Agent or the report.
    for marker in AGENT_NEVER_SHOWS:
        assert marker not in run.text and marker not in model.visible_text(), marker
    for marker in REPORT_NEVER_CONTAINS:
        assert marker not in direct.text, marker

    # Read-only: no write command, no audit lifecycle, no ticket.
    assert after == before
    assert tickets == 0
    assert no_outbound_network == []


def test_the_agent_cannot_turn_operations_analysis_into_a_write(
    settings, runtime_settings, migrated, engine, observed, no_outbound_network
) -> None:
    attempt = CallTool(TICKET_TOOL, {"title": TITLE, "description": DESCRIPTION})
    model = ScriptedToolModel(script=[
        CallTool(REPORT_TOOL, {"business_date": BUSINESS_DATE}), attempt,
        Reply(lambda results: json.dumps(results[TICKET_TOOL])),
    ])  # fmt: skip
    app = create_deployment_app(acceptance_settings(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        before = durable_counts(engine)
        run = client.post(RUNS, headers=OPERATOR, json={
            "message": f"Analyze operations for {BUSINESS_DATE} and create a ticket for the "
                       "failed shipment.", "store_id": SOUTH})  # fmt: skip
        after = durable_counts(engine)
        (desk,) = observed.desks
        tickets = desk.ticket_count

    assert run.status_code == 200
    # The model DID attempt the write tool; the Product refused it on this surface.
    assert model.tool_results()[TICKET_TOOL] == [NOT_REQUESTED]
    assert json.loads(run.json()["message"]) == [NOT_REQUESTED]
    assert after == before  # no write command, no audit lifecycle
    assert tickets == 0  # no ticket
    assert no_outbound_network == []


# ----- acceptance 2: explicit, idempotent, durable write -----------------------------------


def test_explicit_ticket_command_is_verified_idempotent_and_durable(
    settings, runtime_settings, migrated, engine, observed, no_outbound_network
) -> None:
    key = f"mvp-acceptance-{uuid4()}"
    headers = OPERATOR | {"Idempotency-Key": key}
    s = acceptance_settings(settings)

    first_app = create_deployment_app(s, runtime_settings, model=ScriptedToolModel())
    with TestClient(first_app) as client:
        before = durable_counts(engine)
        first = client.post(TICKETS, json=ticket_body(), headers=headers)
        assert first.status_code == 201
        created = first.json()
        outcome = (created["status"], created["reason"], created["replayed"])
        assert outcome == ("verified", "verified", False)
        assert created["persistence_complete"] is True
        command_id, ticket_id = UUID(created["command_id"]), UUID(created["ticket_id"])
        (first_desk,) = observed.desks
        assert first_desk.ticket_count == 1

        # Durable command + complete audit lifecycle in PostgreSQL.
        (command,) = rows_for_key(engine, key)
        assert command["command_id"] == command_id and command["status"] == "verified"
        assert command["execution_reference_id"] == str(ticket_id)
        assert (command["company_id"], command["store_id"], command["actor_id"]) == (
            COMPANY, SOUTH, "mvp-operator-actor",
        )  # fmt: skip
        lifecycle = audit_rows(engine, run_id=command["action_run_id"])
        assert [row["event_type"] for row in lifecycle] == LIFECYCLE
        assert lifecycle[-1]["execution_reference_id"] == str(ticket_id)
        written = durable_counts(engine)
        assert written[0] == before[0] + 1 and written[1] > before[1]

        # Same intent, same key: a replay, not a second execution.
        replay = client.post(TICKETS, json=ticket_body(), headers=headers)
        assert replay.status_code == 200
        again = replay.json()
        assert again["replayed"] is True and again["status"] == "verified"
        assert (again["command_id"], again["ticket_id"]) == (created["command_id"],
                                                             created["ticket_id"])  # fmt: skip
        assert first_desk.ticket_count == 1
        assert durable_counts(engine) == written
        assert audit_rows(engine, run_id=command["action_run_id"]) == lifecycle

        status = client.get(COMMANDS, params={"command_id": str(command_id)}, headers=OPERATOR)

    assert status.status_code == 200
    state = status.json()
    assert (state["command_id"], state["status"], state["reason"], state["ticket_id"]) == (
        str(command_id), "verified", "verified", str(ticket_id),
    )  # fmt: skip
    assert state["created_at"] and state["updated_at"] >= state["created_at"]
    assert "replayed" not in state and "persistence_complete" not in state

    # A NEW application on the SAME database: new engine, new (empty) in-memory desk.
    second_app = create_deployment_app(s, runtime_settings, model=ScriptedToolModel())
    with TestClient(second_app) as client:
        durable = client.get(COMMANDS, params={"command_id": str(command_id)}, headers=OPERATOR)
        after_restart = client.post(TICKETS, json=ticket_body(), headers=headers)
        _, second_desk = observed.desks
        second_tickets = second_desk.ticket_count

    assert durable.status_code == 200
    assert {**durable.json(), "request_id": None} == {**state, "request_id": None}
    # The durable replay survives the restart without re-executing anything.
    assert after_restart.status_code == 200
    replayed = after_restart.json()
    assert replayed["replayed"] is True and replayed["status"] == "verified"
    assert (replayed["command_id"], replayed["ticket_id"]) == (str(command_id), str(ticket_id))
    assert second_tickets == 0  # answered from PostgreSQL, not the mock provider
    assert durable_counts(engine) == written
    assert audit_rows(engine, run_id=command["action_run_id"]) == lifecycle

    for response in (first, replay, status, durable, after_restart):
        assert key not in response.text  # the idempotency key is never echoed
        for marker in AGENT_NEVER_SHOWS:
            assert marker not in response.text, marker
    assert no_outbound_network == []


# ----- acceptance 3: authentication, store scope and command privacy -------------------------


def test_product_auth_and_store_scope_fail_closed(
    settings, runtime_settings, migrated, engine, observed, no_outbound_network
) -> None:
    from tests.conftest import TEST_OS_SECURITY_KEY

    model = ScriptedToolModel()
    wrong = bearer("test-wrong-key-" + "w" * 32)
    agentos = bearer(TEST_OS_SECURITY_KEY)
    run = {"message": "Analyze operations.", "store_id": SOUTH}
    report = {"store_id": SOUTH, "business_date": BUSINESS_DATE}
    app = create_deployment_app(acceptance_settings(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        before = durable_counts(engine)
        assert client.get(HEALTH).status_code == 200  # health needs no Product key

        # No, wrong or AgentOS credentials: 401 on every protected Product route.
        for credentials in (NO_CREDENTIALS, wrong, agentos):
            assert client.post(RUNS, json=run, headers=credentials).status_code == 401
            assert client.get(REPORT, params=report, headers=credentials).status_code == 401
            assert client.post(TICKETS, json=ticket_body(), headers=credentials | {
                "Idempotency-Key": f"mvp-denied-{uuid4()}"}).status_code == 401  # fmt: skip
            assert client.get(COMMANDS, params={"command_id": str(uuid4())},
                              headers=credentials).status_code == 401  # fmt: skip
        assert client.get("/agents", headers=OPERATOR).status_code == 401  # separate credentials

        # A valid key, but a store it is not granted: 403 everywhere, nothing executed.
        foreign_run = client.post(RUNS, json=run | {"store_id": NORTH}, headers=OPERATOR)
        assert foreign_run.status_code == 403
        foreign_report = client.get(REPORT, params=report | {"store_id": NORTH}, headers=OPERATOR)
        assert (foreign_report.status_code, foreign_report.json()) == (403, {"detail": "Forbidden"})
        assert client.post(TICKETS, json=ticket_body() | {"store_id": NORTH}, headers=OPERATOR | {
            "Idempotency-Key": f"mvp-foreign-{uuid4()}"}).status_code == 403  # fmt: skip

        # Invalid requests: the current validation contract.
        assert client.post(RUNS, json={"store_id": SOUTH}, headers=OPERATOR).status_code == 422
        assert client.get(REPORT, params=report | {"business_date": "03/03/2026"},
                          headers=OPERATOR).status_code == 422  # fmt: skip
        assert client.get(REPORT, params=report | {"timezone": "UTC"},
                          headers=OPERATOR).status_code == 422  # fmt: skip
        assert client.post(TICKETS, json=ticket_body(), headers=OPERATOR).status_code == 400
        assert client.post(TICKETS, json=ticket_body() | {"title": ""}, headers=OPERATOR | {
            "Idempotency-Key": f"mvp-invalid-{uuid4()}"}).status_code == 422  # fmt: skip
        assert model.requests == []  # nothing reached the model
        assert durable_counts(engine) == before  # nothing was written or audited

        # Command privacy: another principal's command is indistinguishable from none.
        created = client.post(TICKETS, json=ticket_body(), headers=OPERATOR | {
            "Idempotency-Key": f"mvp-private-{uuid4()}"})  # fmt: skip
        assert created.status_code == 201
        owned = {"command_id": created.json()["command_id"]}
        mine = client.get(COMMANDS, params=owned, headers=OPERATOR)
        theirs = client.get(COMMANDS, params=owned, headers=NORTH_OPERATOR)
        missing = client.get(COMMANDS, params={"command_id": str(uuid4())},
                             headers=NORTH_OPERATOR)  # fmt: skip
    assert mine.status_code == 200
    assert (theirs.status_code, theirs.json()) == (404, {"detail": "Ticket command not found"})
    assert (missing.status_code, missing.json()) == (theirs.status_code, theirs.json())
    assert no_outbound_network == []
