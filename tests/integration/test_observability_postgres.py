"""Product observability over the REAL durable ticket command path (PostgreSQL).

REAL: FastAPI app, middleware, ticket route, WriteCommandTicketService and
WriteCommandTicketQueryService, WriteCommandCoordinator, PostgresWriteCommandStore,
ExecutionCoordinator, GovernanceGate, CreateOperationalTicketHandler and the mock
ticket desk. Test doubles: the ActorResolver, RecordingAuditSink, and recording or
failing observability.
"""

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.application.operations_ticket_queries import WriteCommandTicketQueryService
from app.application.operations_tickets import WriteCommandTicketService
from app.main import create_app
from app.observability import ObservationOutcome, ProductOperation
from tests.integration.product_db import product_store, rows_for_key
from tests.integration.test_ticket_command_idempotency import Instance, TicketSystem
from tests.operations.helpers import STORE, actor
from tests.support.actor_resolver import StaticActorResolver
from tests.support.observability import FAILURE_POINTS, FailingObservability, attrs, harness

pytestmark = pytest.mark.integration

TICKETS, COMMANDS = "/api/v1/operations/tickets", "/api/v1/operations/tickets/commands"
TITLE, DESCRIPTION = "OBS-SECRET-TITLE", "OBS-SECRET-DESCRIPTION"
Out, P = ObservationOutcome, ProductOperation


def new_key() -> str:
    return f"OBS-IDEMPOTENCY-{uuid4()}"


def scenario(settings, runtime_settings, migrated, observability, key):
    """First submission, exact replay and a status read, all over HTTP."""
    system = TicketSystem.build()

    async def main():
        async with product_store(migrated) as store:
            instance = Instance.build(store, system)
            app = create_app(
                settings, runtime_settings, observability=observability,
                actor_resolver=StaticActorResolver(actor()),
                operations_ticket_service=WriteCommandTicketService(instance.commands),
                operations_ticket_query_service=WriteCommandTicketQueryService(store),
            )  # fmt: skip
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                payload = {"store_id": STORE, "title": TITLE, "description": DESCRIPTION}
                headers = {"Idempotency-Key": key}
                first = await client.post(TICKETS, json=payload, headers=headers)
                replay = await client.post(TICKETS, json=payload, headers=headers)
                read = await client.get(COMMANDS, params={"command_id": first.json()["command_id"]})
            return first, replay, read, instance

    first, replay, read, instance = asyncio.run(main())
    return first, replay, read, instance, system


def test_first_and_replay_metadata_and_unchanged_side_effects(
    settings, runtime_settings, migrated, engine
) -> None:
    h, key = harness(), new_key()
    first, replay, read, instance, system = scenario(settings, runtime_settings, migrated,
                                                     h.observability, key)  # fmt: skip
    assert (first.status_code, replay.status_code, read.status_code) == (201, 200, 200)
    assert first.json()["replayed"] is False and replay.json()["replayed"] is True

    services = [log for log in h.logs() if log["operation"] != "http.request"]
    assert [(log["operation"], log["outcome"]) for log in services] == [
        ("operations.ticket_command", "completed"),
        ("operations.ticket_command", "completed"),
        ("operations.ticket_command_query", "completed"),
    ]
    first_log, replay_log, read_log = services
    for log, replayed in ((first_log, False), (replay_log, True)):
        assert (
            log["business_status"],
            log["business_reason"],
            log["replayed"],
            log["persistence_complete"],
        ) == ("verified", "verified", replayed, True)
    assert (read_log["business_status"], read_log["business_reason"]) == ("verified", "verified")
    assert "replayed" not in read_log and "persistence_complete" not in read_log
    http = [log for log in h.logs() if log["operation"] == "http.request"]
    assert [log["http.status_code"] for log in http] == [201, 200, 200]
    for response, log in zip((first, replay, read), http, strict=True):
        assert log["request_id"] == response.headers["X-Request-ID"]

    # Idempotency is unaffected: one execution, one ticket, one command row, one audit run.
    assert instance.executor.calls == 1 and system.desk.ticket_count == 1
    assert len(rows_for_key(engine, key)) == 1
    events = instance.sink.events
    assert [e.event_type.value for e in events][-1] == "verified"
    assert len({e.run_id for e in events}) == 1

    telemetry = repr(h.logs()) + repr([attrs(s) for s in h.finished_spans()])
    telemetry += repr(h.metric_points())
    for marker in ("OBS-SECRET", "OBS-IDEMPOTENCY", first.json()["command_id"],
                   first.json()["ticket_id"], STORE):  # fmt: skip
        assert marker not in telemetry, marker


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_failing_observability_changes_no_write_audit_or_replay(
    settings, runtime_settings, migrated, engine, where
) -> None:
    key = new_key()
    baseline_key = new_key()
    b_first, b_replay, b_read, b_instance, _ = scenario(
        settings, runtime_settings, migrated, harness().observability, baseline_key
    )
    first, replay, read, instance, system = scenario(
        settings, runtime_settings, migrated, FailingObservability(where), key
    )

    def shape(response):
        body = response.json()
        return response.status_code, {k: v for k, v in body.items()
                                      if k not in {"request_id", "command_id", "ticket_id",
                                                   "created_at", "updated_at"}}  # fmt: skip

    assert [shape(r) for r in (first, replay, read)] == [
        shape(r) for r in (b_first, b_replay, b_read)
    ]
    # Exactly once, still a replay, same audit lifecycle, no extra command/ticket/audit.
    assert instance.executor.calls == 1 and system.desk.ticket_count == 1
    assert replay.json()["replayed"] is True
    assert len(rows_for_key(engine, key)) == 1
    assert [e.event_type for e in instance.sink.events] == [
        e.event_type for e in b_instance.sink.events
    ]


def test_observability_alone_creates_nothing(settings, runtime_settings, migrated, engine) -> None:
    """Observing without a Product operation writes no command, audit event or ticket."""
    h = harness()
    system = TicketSystem.build()

    async def main():
        async with product_store(migrated) as store:
            instance = Instance.build(store, system)
            for operation in ProductOperation:
                with h.observability.operation(operation, request_id=uuid4()) as observation:
                    observation.finish(Out.COMPLETED)
            return instance

    instance = asyncio.run(main())
    assert instance.executor.calls == 0 and instance.sink.events == []
    assert system.desk.ticket_count == 0 and system.spy.creates == []
    assert len(h.logs()) == len(ProductOperation)
