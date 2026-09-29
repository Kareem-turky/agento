"""WriteCommandTicketQueryService: the read-only status adapter over WriteCommandReader."""

import asyncio
import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.operations_ticket_queries import WriteCommandTicketQueryService
from app.commands import CommandReason, CommandStatus, WriteCommandReader, WriteCommandRecord
from app.context.models import RequestContext
from app.services import operations_tickets as product
from app.services.operations_tickets import ProductTicketCommandStatusResult
from tests.operations.helpers import COMPANY, OTHER_STORE, STORE, actor, request

S, R = product.TicketCommandStatus, product.TicketCommandReason
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 3, 1, 12, 5, tzinfo=UTC)
TICKET = "0b0b0b0b-0000-4000-8000-000000000001"
READER = actor(permissions=frozenset())  # no tickets.create: reading needs none


def record(**overrides) -> WriteCommandRecord:
    data = {
        "command_id": uuid4(), "company_id": COMPANY, "actor_id": READER.actor_id,
        "store_id": STORE, "action_name": "operations.ticket.create",
        "status": CommandStatus.VERIFIED, "reason": CommandReason.VERIFIED,
        "action_run_id": uuid4(), "execution_reference_id": TICKET, "audit_complete": True,
        "created_at": T0, "updated_at": T1,
    }  # fmt: skip
    return WriteCommandRecord(**(data | overrides))


class ScopedReader:
    """Test reader: applies the principal scope and records every call. It has no
    unscoped ``get``, so an adapter using one would fail loudly."""

    def __init__(self, *records: WriteCommandRecord, raises: BaseException | None = None):
        self.records = {r.command_id: r for r in records}
        self.calls: list[tuple] = []
        self.raises = raises

    async def get_for_actor(self, command_id, company_id, actor_id):
        self.calls.append((command_id, company_id, actor_id))
        if self.raises is not None:
            raise self.raises
        r = self.records.get(command_id)
        return r if r and (r.company_id, r.actor_id) == (company_id, actor_id) else None


def get(reader, command_id: UUID, req: RequestContext | None = None):
    service = WriteCommandTicketQueryService(reader)
    return asyncio.run(service.get_command(req or request(READER), command_id))


def test_reader_protocol_and_scoped_call() -> None:
    rec = record()
    reader = ScopedReader(rec)
    assert isinstance(reader, WriteCommandReader)
    result = get(reader, rec.command_id)
    assert reader.calls == [(rec.command_id, COMPANY, READER.actor_id)]
    assert isinstance(result, ProductTicketCommandStatusResult)
    assert result.model_dump() == {
        "command_id": rec.command_id, "status": S.VERIFIED, "reason": R.VERIFIED,
        "ticket_id": UUID(TICKET), "created_at": T0, "updated_at": T1,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("fields", "status", "reason"),
    [
        ({"status": CommandStatus.IN_PROGRESS, "reason": None, "action_run_id": None,
          "execution_reference_id": None, "audit_complete": None}, S.IN_PROGRESS, None),
        ({"status": CommandStatus.DENIED, "reason": CommandReason.POLICY_DENIED,
          "execution_reference_id": None}, S.DENIED, R.POLICY_DENIED),
        ({"status": CommandStatus.FAILED, "reason": CommandReason.INPUT_INVALID,
          "execution_reference_id": None}, S.FAILED, R.INPUT_INVALID),
        ({"status": CommandStatus.AWAITING_APPROVAL, "reason": CommandReason.APPROVAL_REQUIRED,
          "execution_reference_id": None}, S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED),
        ({"status": CommandStatus.REQUIRES_HUMAN,
          "reason": CommandReason.EXECUTION_OUTCOME_UNCERTAIN, "execution_reference_id": None},
         S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN),
        # A non-verified command WITH an internal reference never exposes it.
        ({"status": CommandStatus.REQUIRES_HUMAN, "reason": CommandReason.VERIFICATION_FAILED},
         S.REQUIRES_HUMAN, R.VERIFICATION_FAILED),
    ],
)  # fmt: skip
def test_durable_states_map_with_ticket_id_only_for_verified(fields, status, reason) -> None:
    rec = record(**fields)
    result = get(ScopedReader(rec), rec.command_id)
    assert (result.status, result.reason, result.ticket_id) == (status, reason, None)


@pytest.mark.parametrize(
    "who_or_record",
    [
        {"record": {"actor_id": "someone-else"}},
        {"record": {"company_id": "0b0b0b0b-0000-4000-8000-00000000c0de"}},
        {"record": {"action_name": "operations.other.create"}},
        {"record": {"store_id": None}},
        {"record": {"store_id": OTHER_STORE}},
        {"actor": {"store_ids": frozenset()}},
        {"actor": {"store_ids": frozenset({"*"})}},
        {"actor": {"store_ids": frozenset({"all"})}},
        {"actor": {"store_ids": frozenset({"stores.*"})}},
        {"actor": {"store_ids": frozenset({STORE.upper()})}},
        {"actor": {"actor_id": "someone-else"}},
    ],
)
def test_everything_invisible_is_the_same_not_found(who_or_record) -> None:
    rec = record(**who_or_record.get("record", {}))
    who = READER.model_copy(update=who_or_record.get("actor", {}))
    with pytest.raises(product.TicketCommandNotFoundError) as info:
        get(ScopedReader(rec), rec.command_id, request(who))
    assert str(info.value) == "ticket_command_not_found"
    assert info.value.__cause__ is None
    for secret in (STORE, COMPANY, TICKET, "operations.other"):
        assert secret not in repr(info.value)


def test_unknown_command_is_not_found() -> None:
    with pytest.raises(product.TicketCommandNotFoundError):
        get(ScopedReader(record()), uuid4())


def test_no_actor_fails_closed_without_reading() -> None:
    reader = ScopedReader(record())
    with pytest.raises(product.TicketCommandNotFoundError):
        get(reader, uuid4(), RequestContext())
    assert reader.calls == []


def test_write_permission_is_not_required() -> None:
    rec = record()
    assert "tickets.create" not in READER.permissions
    assert get(ScopedReader(rec), rec.command_id).status is S.VERIFIED


@pytest.mark.parametrize(
    "error", [RuntimeError("psql://admin:hunter2@db:5432 SENSITIVE"), ConnectionError("x")]
)
def test_reader_failures_are_safe_unavailable(error) -> None:
    with pytest.raises(product.TicketCommandQueryUnavailableError) as info:
        get(ScopedReader(raises=error), uuid4())
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert "hunter2" not in repr(info.value) and "SENSITIVE" not in str(info.value)


class ContractBreakingReader:
    def __init__(self, returns) -> None:
        self.returns = returns

    async def get_for_actor(self, command_id, company_id, actor_id):
        return self.returns


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "verified"},
        "verified",
        record(actor_id="someone-else"),  # a reader ignoring the scope
        record(company_id="another-company"),
        record(execution_reference_id=None),  # verified without a reference
        record(execution_reference_id="tkt_provider_123"),  # not a canonical UUID
    ],
)
def test_contract_breaking_reader_results_fail_closed(bad) -> None:
    command_id = bad.command_id if isinstance(bad, WriteCommandRecord) else uuid4()
    with pytest.raises(product.TicketCommandQueryUnavailableError):
        get(ContractBreakingReader(bad), command_id)


def test_record_for_another_command_id_fails_closed() -> None:
    with pytest.raises(product.TicketCommandQueryUnavailableError):
        get(ContractBreakingReader(record()), uuid4())


def test_durable_persistence_incomplete_reason_fails_closed() -> None:
    # Task 013 never persists it; corrupt storage presenting it must not be shown.
    rec = record(status=CommandStatus.REQUIRES_HUMAN,
                 reason=CommandReason.COMMAND_PERSISTENCE_INCOMPLETE,
                 execution_reference_id=None)  # fmt: skip
    with pytest.raises(product.TicketCommandQueryUnavailableError):
        get(ScopedReader(rec), rec.command_id)


def test_adapter_uses_only_the_scoped_reader() -> None:
    import ast

    from app.application import operations_ticket_queries as module

    tree = ast.parse(inspect.getsource(module))
    reader_calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_reader"
    }  # fmt: skip
    assert reader_calls == {"get_for_actor"}
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"get", "claim", "complete", "submit", "run"}
    params = inspect.signature(module.WriteCommandTicketQueryService.__init__).parameters
    assert list(params) == ["self", "reader"]
