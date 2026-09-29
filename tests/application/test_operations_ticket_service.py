"""WriteCommandTicketService: the application adapter over WriteCommandCoordinator.

Real ExecutionCoordinator, GovernanceGate, CreateOperationalTicketHandler and mock
ticketing; the durable store is the in-memory test fake (PostgreSQL is covered by
the HTTP E2E in tests/integration).
"""

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.application.operations_tickets import WriteCommandTicketService
from app.commands import (
    CommandReason,
    CommandStatus,
    WriteCommandCoordinator,
    WriteCommandResult,
    WriteCommandStoreError,
)
from app.execution import ActionHandlerRegistry
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.integrations.commerce.mock import MockTicketWriteMode
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.services import operations_tickets as product
from app.services.operations_tickets import ProductTicketCommandResult
from tests.commands.fakes import CountingExecutionCoordinator, InMemoryWriteCommandStore
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink
from tests.operations.helpers import COMPANY, STORE, SpyTicketing, actor, request
from tests.operations.helpers import stack as ticket_stack

S, R = product.TicketCommandStatus, product.TicketCommandReason
KEY = "Key-SENSITIVE-7c1e"
TITLE, DESCRIPTION = "Failed delivery", "Shipment requires operations follow-up."
SCOPE = ActionScope(company_id=COMPANY, store_id=STORE)


class RecordingCoordinator:
    """Captures what the adapter submits; returns or raises what it is told to."""

    def __init__(self, returns: Any = None, raises: BaseException | None = None) -> None:
        self.calls: list[tuple] = []
        self.returns, self.raises = returns, raises

    async def submit(self, *args: Any) -> Any:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.returns


class Env:
    def __init__(self, mode=MockTicketWriteMode.NORMAL, store=None) -> None:
        base = ticket_stack(mode)
        self.desk, self.adapter = base.desk, base.adapter
        self.spy = SpyTicketing(base.adapter)
        self.store = store or InMemoryWriteCommandStore()
        self.executor = CountingExecutionCoordinator(
            GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
            ActionHandlerRegistry([CreateOperationalTicketHandler(self.spy)]),
            RecordingAuditSink(), clock=lambda: FIXED_TIME,
        )  # fmt: skip
        self.service = WriteCommandTicketService(
            WriteCommandCoordinator(self.store, self.executor, ActionCatalog(OPERATIONS_ACTIONS))
        )

    def create(self, *, req=None, title=TITLE, description=DESCRIPTION, key=KEY):
        return asyncio.run(
            self.service.create_ticket(req or request(), SCOPE, title, description, key)
        )


def command_result(**overrides) -> WriteCommandResult:
    data = {
        "command_id": UUID(int=1), "action_name": "operations.ticket.create",
        "status": CommandStatus.VERIFIED, "reason": CommandReason.VERIFIED,
        "action_run_id": UUID(int=2),
        "execution_reference_id": "0b0b0b0b-0000-4000-8000-000000000001",
        "audit_complete": True, "replayed": False, "persistence_complete": True,
    }  # fmt: skip
    return WriteCommandResult(**(data | overrides))


def submit_with(coordinator: RecordingCoordinator, **kwargs):
    service = WriteCommandTicketService(coordinator)  # type: ignore[arg-type]
    args = {"request": request(), "scope": SCOPE, "title": TITLE, "description": DESCRIPTION,
            "idempotency_key": KEY} | kwargs  # fmt: skip
    return asyncio.run(service.create_ticket(**args))


def test_always_submits_the_fixed_ticket_action_with_only_title_and_description() -> None:
    coord = RecordingCoordinator(returns=command_result())
    req = request()
    submit_with(coord, request=req)
    ((got_request, got_scope, intent, parameters, key),) = coord.calls
    assert got_request is req and got_scope is SCOPE
    assert intent.name == "operations.ticket.create"
    assert parameters == {"title": TITLE, "description": DESCRIPTION}
    assert key == KEY  # unchanged


def test_signature_has_no_action_name() -> None:
    import inspect

    params = inspect.signature(WriteCommandTicketService.create_ticket).parameters
    assert list(params) == ["self", "request", "scope", "title", "description",
                            "idempotency_key"]  # fmt: skip


def test_verified_maps_to_the_canonical_ticket_uuid() -> None:
    env = Env()
    result = env.create()
    assert isinstance(result, ProductTicketCommandResult)
    assert (result.status, result.reason, result.replayed) == (S.VERIFIED, R.VERIFIED, False)
    (call,) = env.spy.creates
    ticket = asyncio.run(env.adapter.get_ticket(result.ticket_id))
    assert ticket.id == result.ticket_id and str(ticket.store_id) == STORE
    assert call["title"] == TITLE and env.executor.calls == 1
    replay = env.create()
    assert (replay.command_id, replay.ticket_id, replay.replayed) == (
        result.command_id, result.ticket_id, True,
    )  # fmt: skip
    assert env.executor.calls == 1 and env.desk.ticket_count == 1


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (CommandStatus.REQUIRES_HUMAN, CommandReason.VERIFICATION_FAILED),
        (CommandStatus.REQUIRES_HUMAN, CommandReason.AUDIT_INCOMPLETE),
        (CommandStatus.REQUIRES_HUMAN, CommandReason.VERIFICATION_ERROR),
    ],
)
def test_non_verified_suppresses_the_internal_execution_reference(status, reason) -> None:
    coord = RecordingCoordinator(returns=command_result(status=status, reason=reason))
    result = submit_with(coord)
    assert result.ticket_id is None and result.status.value == status.value


def test_uncertain_write_has_no_ticket_id_even_though_a_ticket_exists() -> None:
    env = Env(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)
    result = env.create()
    assert (result.status, result.reason, result.ticket_id) == (
        S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN, None,
    )  # fmt: skip
    assert env.desk.ticket_count == 1


@pytest.mark.parametrize(
    "bad",
    [
        command_result(execution_reference_id=None),
        command_result(execution_reference_id="tkt_provider_123"),
        command_result(action_name="operations.other.create"),
        None,
        {"status": "verified"},
        "verified",
    ],
)
def test_invalid_command_results_fail_closed(bad) -> None:
    with pytest.raises(product.TicketCommandUnavailableError) as info:
        submit_with(RecordingCoordinator(returns=bad))
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert str(info.value) == "ticket_command_unavailable"


def test_denied_invalid_input_and_missing_permission_map_safely() -> None:
    env = Env()
    denied = env.create(req=request(actor(permissions=frozenset())))
    assert (denied.status, denied.reason, denied.ticket_id) == (S.DENIED, R.POLICY_DENIED, None)
    failed = env.create(title="   ", key="other-key")
    assert (failed.status, failed.reason, failed.ticket_id) == (S.FAILED, R.INPUT_INVALID, None)
    assert env.desk.ticket_count == 0


def test_conflict_invalid_key_and_store_failure_map_to_safe_product_errors() -> None:
    env = Env()
    env.create()
    with pytest.raises(product.IdempotencyConflictError) as conflict:
        env.create(title="Changed title")
    for bad_key in ("", "has space", "k" * 129, "ключ"):
        with pytest.raises(product.InvalidIdempotencyKeyError) as invalid:
            env.create(key=bad_key)
    down = Env(store=InMemoryWriteCommandStore(fail_claim=True))
    with pytest.raises(product.TicketCommandUnavailableError) as unavailable:
        down.create()
    for info in (conflict, invalid, unavailable):
        assert info.value.__cause__ is None and info.value.__suppress_context__
        text = repr(info.value) + str(info.value)
        assert KEY not in text and "SENSITIVE" not in text and TITLE not in text
    assert env.desk.ticket_count == 1 and down.executor.calls == 0


@pytest.mark.parametrize(
    "error",
    [
        WriteCommandStoreError(),
        RuntimeError("db at 10.0.0.5 password=hunter2 SENSITIVE"),
        KeyError("SENSITIVE"),
    ],
)
def test_unexpected_command_layer_errors_never_cross_the_contract(error) -> None:
    with pytest.raises(product.TicketCommandUnavailableError) as info:
        submit_with(RecordingCoordinator(raises=error))
    assert info.value.__cause__ is None
    assert "SENSITIVE" not in repr(info.value) and "hunter2" not in str(info.value)


def test_persistence_incomplete_and_execution_error_map_with_null_ticket() -> None:
    env = Env(store=InMemoryWriteCommandStore(fail_complete=True))
    incomplete = env.create()
    assert (incomplete.status, incomplete.reason, incomplete.ticket_id) == (
        S.REQUIRES_HUMAN, R.COMMAND_PERSISTENCE_INCOMPLETE, None,
    )  # fmt: skip
    assert incomplete.persistence_complete is False
    env.store.fail_complete = False
    retry = env.create()
    assert (retry.status, retry.reason, retry.replayed, retry.ticket_id) == (
        S.IN_PROGRESS, None, True, None,
    )  # fmt: skip
    assert env.desk.ticket_count == 1 and env.executor.calls == 1
