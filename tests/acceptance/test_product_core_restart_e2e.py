"""PRODUCT CORE ACCEPTANCE (Task 040), journey J: a durability snapshot across a restart.

One installation creates a meaningful set of Product state through the Product: an Agent
configuration, an explicit verified WriteCommand with its audit lifecycle, a Workflow run,
a Knowledge document, an Approval request with its decision, a Conversation message and
Integration connection metadata. A NEW installation (new engines, new in-memory mock
desk, new process objects) on the SAME PostgreSQL database then reads every one of them
back through the Product API, and the idempotent write replays from PostgreSQL without
executing again. TEST-ONLY in-memory provider objects and counters are NOT restart
evidence: they are rebuilt empty, by design.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.conversations.models import ChannelContext, InboundMessageEnvelope
from tests.integration.product_db import audit_rows, rows_for_key
from tests.support.canonical_mock import BUSINESS_DATE, COMPANY, SOUTH
from tests.support.product_core import (
    DECIDER,
    OPERATOR,
    CoreInstallation,
    GovernedTestProcess,
    RealClock,
    rows,
    wipe_agent_configuration,
)

pytestmark = pytest.mark.integration

TICKETS, COMMANDS = "/api/v1/operations/tickets", "/api/v1/operations/tickets/commands"
OPS = {"agent_id": "operations"}
LIFECYCLE = ["requested", "policy_decided", "execution_started", "execution_completed",
             "verification_started", "verified"]  # fmt: skip


@pytest.fixture
def clean_agents(engine: sa.Engine):
    wipe_agent_configuration(engine)
    yield
    wipe_agent_configuration(engine)


def daily_runs(engine: sa.Engine) -> set[UUID]:
    return {r["run_id"] for r in rows(
        engine, "SELECT run_id FROM product.workflow_runs WHERE company_id = :c AND "
        "workflow_id = 'operations.daily_report'", c=COMPANY)}  # fmt: skip


def test_product_state_survives_a_restart_on_the_same_database(
    core: CoreInstallation, engine: sa.Engine, migrated: str, clean_agents, no_outbound_network
) -> None:
    key = f"core-restart-{uuid4()}"
    ticket = {"store_id": SOUTH, "title": "Restart snapshot ticket",
              "description": "Created before the restart; replayed after it."}  # fmt: skip
    thread = f"core-restart-thread-{uuid4().hex}"
    process = GovernedTestProcess(migrated, RealClock())
    try:
        pending = asyncio.run(process.request_budget(campaign="winter"))
        known_runs = daily_runs(engine)
        with TestClient(core.app()) as client:
            assert client.post("/api/v1/agents/agent/disable", headers=OPERATOR,
                               params=OPS).status_code == 200  # fmt: skip
            created = client.post(TICKETS, headers=OPERATOR | {"Idempotency-Key": key},
                                  json=ticket)  # fmt: skip
            report = client.get(
                "/api/v1/operations/reports/daily",
                headers=OPERATOR,
                params={"store_id": SOUTH, "business_date": BUSINESS_DATE},
            )
            document = client.post("/api/v1/knowledge/document/create", headers=OPERATOR, json={
                "category": "sop", "title": "Restart SOP", "content_type": "text/plain",
                "body": "Durable operating procedure."})  # fmt: skip
            connection = client.post("/api/v1/integrations/connections", headers=OPERATOR,
                                     json={"integration_id": "example-chat",
                                           "display_name": "Restart inbox"})  # fmt: skip
            cid = UUID(connection.json()["connection"]["connection_id"])
            inbound = client.portal.call(  # type: ignore[union-attr]
                core.ingress.ingest, ChannelContext(company_id=COMPANY, connection_id=cid,
                                                    store_id=SOUTH),
                InboundMessageEnvelope(external_conversation_ref=thread,
                                       external_message_ref="restart-1", text="Still here?",
                                       occurred_at=datetime(2031, 7, 1, tzinfo=UTC)))  # fmt: skip
            approved = client.post("/api/v1/approvals/approval/approve", headers=DECIDER,
                                   params={"approval_id": str(pending.approval_id)},
                                   json={"note": "Approved before the restart."})  # fmt: skip
            (first_desk,) = core.desks
            tickets_before = first_desk.ticket_count
    finally:
        asyncio.run(process.close())

    assert created.status_code == 201 and created.json()["status"] == "verified"
    assert report.status_code == 200 and document.status_code == 200
    assert approved.status_code == 200 and tickets_before == 1
    (run_id,) = daily_runs(engine) - known_runs
    command_id, document_id = created.json()["command_id"], document.json()["document"][
        "document_id"]  # fmt: skip
    conversation_id = str(inbound.conversation.conversation_id)
    (command,) = rows_for_key(engine, key)
    lifecycle = audit_rows(engine, run_id=command["action_run_id"])
    assert [r["event_type"] for r in lifecycle] == LIFECYCLE

    # ---- restart: a NEW installation on the SAME database ----------------------------------
    with TestClient(core.app()) as client:
        agent = client.get("/api/v1/agents/agent", headers=OPERATOR, params=OPS).json()
        status = client.get(COMMANDS, headers=OPERATOR, params={"command_id": command_id})
        replay = client.post(TICKETS, headers=OPERATOR | {"Idempotency-Key": key}, json=ticket)
        run = client.get("/api/v1/workflows/run", headers=OPERATOR, params={"run_id": str(run_id)})
        doc = client.get("/api/v1/knowledge/document", headers=OPERATOR,
                         params={"document_id": document_id})  # fmt: skip
        approval = client.get("/api/v1/approvals/approval", headers=DECIDER,
                              params={"approval_id": str(pending.approval_id)})  # fmt: skip
        messages = client.get("/api/v1/conversations/messages", headers=OPERATOR,
                              params={"conversation_id": conversation_id})  # fmt: skip
        metadata = client.get("/api/v1/integrations/connection", headers=OPERATOR,
                              params={"connection_id": str(cid)})  # fmt: skip
        _, second_desk = core.desks
        tickets_after = second_desk.ticket_count

    assert agent["agent"]["state"]["enabled"] is False  # Agent configuration
    assert (status.status_code, status.json()["status"]) == (200, "verified")  # WriteCommand
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["command_id"] == command_id
    assert tickets_after == 0  # answered from PostgreSQL, the provider was not called again
    assert audit_rows(engine, run_id=command["action_run_id"]) == lifecycle  # Audit lifecycle
    assert (run.json()["run"]["workflow_id"], run.json()["run"]["status"]) == (
        "operations.daily_report", "succeeded")  # Workflow state  # fmt: skip
    assert doc.status_code == 200 and doc.json()["document"]["title"] == "Restart SOP"
    assert (approval.json()["approval"]["status"],
            approval.json()["approval"]["consumed"]) == ("approved", False)  # fmt: skip
    assert [m["text"] for m in messages.json()["messages"]] == ["Still here?"]
    assert metadata.json()["connection"]["display_name"] == "Restart inbox"
    assert no_outbound_network == []
