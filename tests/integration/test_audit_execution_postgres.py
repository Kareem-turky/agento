"""Durable audit through the REAL execution and write-command paths.

REAL: ExecutionCoordinator, GovernanceGate, CreateOperationalTicketHandler,
MockTicketingAdapter/MockTicketDesk, WriteCommandCoordinator, PostgresWriteCommandStore
and PostgresAuditSink (PostgreSQL). Audit failures are REAL sink failures: for the chosen
event type the event goes to a PostgresAuditSink whose database is unreachable.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest

from app.commands import CommandStatus, WriteCommandCoordinator
from app.execution import (
    ActionHandlerRegistry,
    ActionRunReason,
    ActionRunStatus,
    AuditEvent,
    AuditEventType,
    ExecutionCoordinator,
)
from app.governance import ActionCatalog, ActionIntent, ActionScope, GovernanceGate
from app.integrations.commerce.mock import (
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
)
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.persistence import AuditPersistenceError, PostgresAuditSink, create_session_factory
from app.persistence.database import create_product_engine
from tests.integration.product_db import audit_rows, audit_sink, product_store, rows_for_key
from tests.operations.helpers import ACTION, COMPANY, STORE, SpyTicketing, actor, request

pytestmark = pytest.mark.integration
S, R, E = ActionRunStatus, ActionRunReason, AuditEventType
UNREACHABLE = "postgresql+psycopg://audit:audit@127.0.0.1:1/audit"

TITLE_MARKER = "AUDITLEAK-TITLE-5d1e"
DESCRIPTION_MARKER = "AUDITLEAK-DESCRIPTION-a0c4"
SECRET_MARKER = "AUDITLEAK-test-product-secret-9b7f"  # noqa: S105 - test-only marker
PROVIDER_MARKER = "AUDITLEAK-provider-says-2e61"
PARAMS = {
    "title": f"Parcel delayed {TITLE_MARKER}",
    "description": f"No scan {DESCRIPTION_MARKER} {SECRET_MARKER} {PROVIDER_MARKER}",
}
MARKERS = (TITLE_MARKER, DESCRIPTION_MARKER, SECRET_MARKER, PROVIDER_MARKER, "AUDITLEAK")


class FailingOn:
    """Routes one event type to a sink whose database is down; the rest are durable."""

    def __init__(self, durable: PostgresAuditSink, broken: PostgresAuditSink, fail_on):
        self.durable, self.broken, self.fail_on = durable, broken, fail_on
        self.failures: list[AuditEventType] = []

    async def record(self, event: AuditEvent) -> None:
        if event.event_type is self.fail_on:
            try:
                await self.broken.record(event)
            except AuditPersistenceError:
                self.failures.append(event.event_type)
                raise
            raise AssertionError("the unreachable sink unexpectedly succeeded")
        await self.durable.record(event)


@asynccontextmanager
async def broken_sink():
    engine = create_product_engine(UNREACHABLE)
    try:
        yield PostgresAuditSink(create_session_factory(engine))
    finally:
        await engine.dispose()


def tickets(mode=MockTicketWriteMode.NORMAL):
    desk = MockTicketDesk(mode=mode)
    adapter = MockTicketingAdapter(MockCommerceSystem(), desk)
    return desk, adapter, SpyTicketing(adapter)


def executor(sink, spy) -> ExecutionCoordinator:
    return ExecutionCoordinator(
        GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
        ActionHandlerRegistry([CreateOperationalTicketHandler(spy)]),
        sink,
    )  # default (real) clock


def execute(url, *, mode=MockTicketWriteMode.NORMAL, fail_on=None, params=None, req=None):
    desk, adapter, spy = tickets(mode)

    async def scenario():
        async with audit_sink(url) as durable, broken_sink() as broken:
            sink = durable if fail_on is None else FailingOn(durable, broken, fail_on)
            result = await executor(sink, spy).run(
                req or request(), ActionIntent(name=ACTION),
                ActionScope(company_id=COMPANY, store_id=STORE),
                PARAMS if params is None else params,
            )  # fmt: skip
            return result, sink

    result, sink = asyncio.run(scenario())
    return result, sink, desk, spy


def types(engine, run_id: UUID) -> list[str]:
    return [row["event_type"] for row in audit_rows(engine, run_id=run_id)]


def assert_no_markers(rows) -> None:
    text = json.dumps(rows, default=str)
    for marker in MARKERS:
        assert marker not in text, marker


def test_successful_lifecycle_is_durable_and_metadata_only(migrated, engine) -> None:
    result, _, desk, spy = execute(migrated)
    assert (result.status, result.reason, result.audit_complete) == (S.VERIFIED, R.VERIFIED, True)
    rows = audit_rows(engine, run_id=result.run_id)
    assert [r["event_type"] for r in rows] == [
        "requested", "policy_decided", "execution_started", "execution_completed",
        "verification_started", "verified",
    ]  # fmt: skip
    assert len({r["event_id"] for r in rows}) == 6
    assert {r["request_id"] for r in rows} == {result.request_id}
    assert all(r["recorded_at"].tzinfo is not None for r in rows)
    assert all((r["company_id"], r["store_id"], r["action_name"]) == (COMPANY, STORE, ACTION)
               for r in rows)  # fmt: skip
    assert (
        rows[-1]["run_status"] == "verified" and rows[-1]["verification_code"] == "ticket_present"
    )
    assert rows[-1]["execution_reference_id"] == result.execution_result.reference_id
    assert desk.ticket_count == 1 and len(spy.creates) == 1
    assert_no_markers(rows)


def test_execution_started_failure_prevents_the_side_effect(migrated, engine) -> None:
    result, sink, desk, spy = execute(migrated, fail_on=E.EXECUTION_STARTED)
    assert (result.status, result.reason) == (S.FAILED, R.AUDIT_UNAVAILABLE)
    assert sink.failures == [E.EXECUTION_STARTED]
    assert desk.ticket_count == 0 and spy.creates == [] and spy.correlation_reads == []
    assert result.execution_result is None and result.verification_result is None
    assert types(engine, result.run_id) == ["requested", "policy_decided"]


def test_post_side_effect_failure_never_reports_verified(migrated, engine) -> None:
    result, sink, desk, spy = execute(migrated, fail_on=E.EXECUTION_COMPLETED)
    assert result.status is not S.VERIFIED
    assert (result.status, result.reason, result.audit_complete) == (
        S.REQUIRES_HUMAN, R.AUDIT_INCOMPLETE, False,
    )  # fmt: skip
    assert sink.failures == [E.EXECUTION_COMPLETED]
    # The side effect happened exactly once, and verification still ran.
    assert desk.ticket_count == 1 and len(spy.creates) == 1
    assert spy.correlation_reads == [result.run_id]
    assert result.verification_result.verified is True
    recorded = types(engine, result.run_id)
    assert recorded[:3] == ["requested", "policy_decided", "execution_started"]
    assert "execution_completed" not in recorded and "verified" not in recorded
    assert recorded[-1] == "requires_human"
    (last,) = [r for r in audit_rows(engine, run_id=result.run_id)
               if r["event_type"] == "requires_human"]  # fmt: skip
    assert (last["run_status"], last["run_reason"]) == ("requires_human", "audit_incomplete")


def test_denied_run_is_audited(migrated, engine) -> None:
    result, _, desk, spy = execute(migrated, req=request(actor(permissions=frozenset())))
    assert result.status is S.DENIED
    rows = audit_rows(engine, run_id=result.run_id)
    assert [r["event_type"] for r in rows] == ["requested", "policy_decided", "denied"]
    assert rows[1]["policy_outcome"] == "deny" and rows[1]["policy_reason"] == "permission_denied"
    assert desk.ticket_count == 0 and spy.creates == []


def test_invalid_input_is_audited_without_the_input(migrated, engine) -> None:
    bad = {"title": f"t {TITLE_MARKER}", "description": "d", "priority": SECRET_MARKER}
    result, _, desk, spy = execute(migrated, params=bad)
    assert (result.status, result.reason) == (S.FAILED, R.INPUT_INVALID)
    rows = audit_rows(engine, run_id=result.run_id)
    assert [r["event_type"] for r in rows] == ["requested", "policy_decided", "validation_failed"]
    assert desk.ticket_count == 0 and spy.creates == []
    assert_no_markers(rows)


def test_uncertain_write_is_audited_and_needs_a_human(migrated, engine) -> None:
    result, _, desk, spy = execute(migrated, mode=MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    rows = audit_rows(engine, run_id=result.run_id)
    assert [r["event_type"] for r in rows][-4:] == [
        "execution_started", "execution_failed", "verification_started", "requires_human",
    ]  # fmt: skip
    assert "verified" not in [r["event_type"] for r in rows]
    assert desk.ticket_count == 1 and len(spy.creates) == 1
    assert_no_markers(rows)


def test_write_command_with_durable_audit_and_exact_replay(migrated, engine) -> None:
    key = f"AUDITLEAK-idempotency-{uuid4()}"
    desk, _, spy = tickets()

    async def scenario():
        async with product_store(migrated) as store, audit_sink(migrated) as sink:
            commands = WriteCommandCoordinator(
                store, executor(sink, spy), ActionCatalog(OPERATIONS_ACTIONS)
            )
            submit = lambda: commands.submit(  # noqa: E731
                request(), ActionScope(company_id=COMPANY, store_id=STORE),
                ActionIntent(name=ACTION), PARAMS, key,
            )  # fmt: skip
            first = await submit()
            rows_after_first = audit_rows(engine)
            replay = await submit()
            return first, rows_after_first, replay

    first, rows_after_first, replay = asyncio.run(scenario())
    assert (first.status, first.replayed, first.audit_complete) == (CommandStatus.VERIFIED, False,
                                                                     True)  # fmt: skip
    (command,) = rows_for_key(engine, key)
    assert command["status"] == "verified" and command["action_run_id"] == first.action_run_id
    rows = audit_rows(engine, run_id=first.action_run_id)
    assert [r["event_type"] for r in rows] == [
        "requested", "policy_decided", "execution_started", "execution_completed",
        "verification_started", "verified",
    ]  # fmt: skip
    assert desk.ticket_count == 1 and len(spy.creates) == 1

    # Exact replay: no execution, no new audit lifecycle, no second ticket.
    assert replay.replayed is True and replay.command_id == first.command_id
    assert replay.action_run_id == first.action_run_id
    assert audit_rows(engine) == rows_after_first
    assert desk.ticket_count == 1 and len(spy.creates) == 1
    assert len(rows_for_key(engine, key)) == 1

    # Data minimization across every audit row of the run: no parameters, no
    # idempotency key, no Product-API-like secret, no provider-shaped text or keys.
    assert_no_markers(rows)
    text = json.dumps(rows, default=str)
    assert key not in text and "tkt_" not in text and "idempotency" not in text
