"""operations.ticket.create through durable write commands, end to end (not HTTP).

REAL: RequestContext, ActorContext, ActionScope, WriteCommandCoordinator,
PostgresWriteCommandStore (PostgreSQL), ExecutionCoordinator, GovernanceGate,
CreateOperationalTicketHandler, MockTicketingAdapter and MockTicketDesk.
Test doubles: RecordingAuditSink, and an ExecutionCoordinator subclass that only
counts entries. Each test uses a fresh random idempotency key, so reruns against the
same database never replay each other.
"""

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.commands import (
    CommandReason,
    CommandStatus,
    IdempotencyConflictError,
    WriteCommandCoordinator,
    WriteCommandResult,
    hash_idempotency_key,
    request_fingerprint,
)
from app.context.models import RequestContext
from app.execution import ActionHandlerRegistry
from app.governance import (
    ActionCatalog,
    ActionDefinition,
    ActionIntent,
    ActionRisk,
    ActionScope,
    ActionScopeRequirement,
    GovernanceGate,
)
from app.integrations.commerce.mock import (
    MockCommerceSystem,
    MockTicketDesk,
    MockTicketingAdapter,
    MockTicketWriteMode,
)
from app.operations import OPERATIONS_ACTIONS, CreateOperationalTicketHandler
from app.persistence import PostgresWriteCommandStore
from tests.commands.fakes import CountingExecutionCoordinator
from tests.execution.fakes import FIXED_TIME, RecordingAuditSink
from tests.integration.product_db import product_store, rows_for_key
from tests.operations.helpers import (
    ACTION,
    COMPANY,
    OTHER_STORE,
    STORE,
    SpyTicketing,
    actor,
    request,
)

pytestmark = pytest.mark.integration
S, R = CommandStatus, CommandReason

TITLE_MARKER = "SENSITIVE-TITLE-7f3a"
DESCRIPTION_MARKER = "SENSITIVE-DESCRIPTION-91bc"
PARAMS = {"title": f"Parcel delayed {TITLE_MARKER}", "description": f"No scan {DESCRIPTION_MARKER}"}
# Another write action, only to prove a key reused for a different action conflicts.
NOTE_ACTION = ActionDefinition(
    name="operations.test_note.add",
    description="Test-only write action (no handler).",
    risk=ActionRisk.LOW_RISK_WRITE,
    required_permission="tickets.create",
    scope_requirement=ActionScopeRequirement.STORE,
)
CATALOG = ActionCatalog((*OPERATIONS_ACTIONS, NOTE_ACTION))


def new_key() -> str:
    return f"SENSITIVEKEY-{uuid4()}"


class SlowTicketing(SpyTicketing):
    """Delays the provider write so concurrent submissions overlap with it."""

    async def create_ticket(self, **kwargs):
        await asyncio.sleep(0.3)
        return await super().create_ticket(**kwargs)


@dataclass
class TicketSystem:
    """The external ticket system: shared by every coordinator 'instance'."""

    desk: MockTicketDesk
    spy: SpyTicketing

    @classmethod
    def build(cls, mode=MockTicketWriteMode.NORMAL, *, slow: bool = False) -> "TicketSystem":
        desk = MockTicketDesk(mode=mode)
        adapter = MockTicketingAdapter(MockCommerceSystem(), desk)
        return cls(desk, (SlowTicketing if slow else SpyTicketing)(adapter))


@dataclass
class Instance:
    """One application instance: its own executor, audit sink and command coordinator."""

    executor: CountingExecutionCoordinator
    sink: RecordingAuditSink
    commands: WriteCommandCoordinator

    @classmethod
    def build(cls, store, system: TicketSystem, **executor_options) -> "Instance":
        sink = RecordingAuditSink()
        executor = CountingExecutionCoordinator(
            GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS)),
            ActionHandlerRegistry([CreateOperationalTicketHandler(system.spy)]),
            sink,
            clock=lambda: FIXED_TIME,
            **executor_options,
        )
        return cls(executor, sink, WriteCommandCoordinator(store, executor, CATALOG))

    async def submit(
        self,
        key: str,
        params=None,
        *,
        req: RequestContext | None = None,
        store_id: str = STORE,
        action: str = ACTION,
    ) -> WriteCommandResult:
        return await self.commands.submit(
            req or request(),
            ActionScope(company_id=COMPANY, store_id=store_id),
            ActionIntent(name=action),
            PARAMS if params is None else params,
            key,
        )


def test_first_submission_and_completed_replay(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()

    async def scenario():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            first = await app.submit(key)
            events_after_first = list(app.sink.events)
            second = await app.submit(key)
            return app, first, events_after_first, second

    app, first, events_after_first, second = asyncio.run(scenario())

    # First submission: NEW claim, executed once, verified, persisted VERIFIED.
    assert (first.status, first.reason, first.replayed, first.persistence_complete) == (
        S.VERIFIED, R.VERIFIED, False, True,
    )  # fmt: skip
    assert first.audit_complete is True and first.action_run_id is not None
    (ticket_call,) = system.spy.creates
    assert first.execution_reference_id is not None
    ticket_id = UUID(first.execution_reference_id)  # canonical ticket UUID
    assert ticket_call["correlation_id"] == first.action_run_id
    assert system.desk.ticket_count == 1
    assert events_after_first[0].run_id == first.action_run_id
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "verified" and row["action_run_id"] == first.action_run_id
    assert row["execution_reference_id"] == str(ticket_id)
    assert (row["company_id"], row["actor_id"], row["store_id"]) == (COMPANY, "ops-user-1", STORE)

    # Completed replay: same command, nothing executed, no second audit sequence.
    assert second.replayed is True and second.persistence_complete is True
    assert second.command_id == first.command_id
    assert (second.status, second.execution_reference_id) == (S.VERIFIED, str(ticket_id))
    assert second.action_run_id == first.action_run_id
    assert app.executor.calls == 1
    assert app.sink.events == events_after_first
    assert len(system.spy.creates) == 1 and system.desk.ticket_count == 1
    assert len(rows_for_key(engine, key)) == 1


def test_cross_instance_and_restart_replay(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()

    async def instance_a():
        async with product_store(migrated) as store:
            return await Instance.build(store, system).submit(key)

    first = asyncio.run(instance_a())  # instance A finishes and is discarded (engine disposed)

    async def instance_b():
        # A separate engine, pool and connection: a different process / a restart.
        async with product_store(migrated) as store:
            app_b = Instance.build(store, system)
            return app_b, await app_b.submit(key)

    app_b, replay = asyncio.run(instance_b())
    assert replay.replayed is True and replay.command_id == first.command_id
    assert (replay.status, replay.execution_reference_id) == (
        S.VERIFIED, first.execution_reference_id,
    )  # fmt: skip
    assert app_b.executor.calls == 0 and app_b.sink.events == []
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    assert len(rows_for_key(engine, key)) == 1


def test_concurrent_business_submissions_execute_at_most_once(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build(slow=True)
    submitters = 6

    async def one():
        async with product_store(migrated) as store:  # separate instances
            app = Instance.build(store, system)
            return app, await app.submit(key)

    async def scenario():
        return await asyncio.gather(*(one() for _ in range(submitters)))

    outcomes = asyncio.run(scenario())
    results = [r for _, r in outcomes]
    assert sum(app.executor.calls for app, _ in outcomes) == 1
    fresh = [r for r in results if not r.replayed]
    assert len(fresh) == 1 and fresh[0].status is S.VERIFIED
    replays = [r for r in results if r.replayed]
    assert len(replays) == submitters - 1
    assert {r.status for r in replays} <= {S.IN_PROGRESS, S.VERIFIED}
    assert S.IN_PROGRESS in {r.status for r in replays}  # overlapped the slow write
    assert {r.command_id for r in results} == {fresh[0].command_id}
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "verified"


@pytest.mark.parametrize(
    "change",
    [
        {"params": {**PARAMS, "title": "Changed title"}},
        {"params": {**PARAMS, "description": "Changed description"}},
        {"store_id": OTHER_STORE},
        {"action": NOTE_ACTION.name},
    ],
    ids=["title", "description", "store", "action"],
)
def test_same_key_for_a_different_request_conflicts(migrated, engine, change) -> None:
    key, system = new_key(), TicketSystem.build()
    other_store_actor = actor(store_ids=frozenset({STORE, OTHER_STORE}))

    async def scenario():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            first = await app.submit(key, req=request(other_store_actor))
            with pytest.raises(IdempotencyConflictError) as info:
                await app.submit(key, req=request(other_store_actor), **change)
            return app, first, info.value

    app, first, error = asyncio.run(scenario())
    assert first.status is S.VERIFIED
    assert str(error) == "idempotency_conflict"
    assert key not in repr(error) and TITLE_MARKER not in repr(error)
    assert app.executor.calls == 1
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    (row,) = rows_for_key(engine, key)
    assert row["command_id"] == first.command_id and row["status"] == "verified"


def test_denied_command_replays_even_after_permission_is_granted(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()
    no_permission = request(actor(permissions=frozenset()))
    with_permission = request(actor())

    async def scenario():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            denied = await app.submit(key, req=no_permission)
            calls_after_denied = app.executor.calls
            replay = await app.submit(key, req=no_permission)
            later = await app.submit(key, req=with_permission)  # permission granted since
            fresh = await app.submit(new_key(), req=with_permission)  # new key: new attempt
            return app, calls_after_denied, denied, replay, later, fresh

    app, calls_after_denied, denied, replay, later, fresh = asyncio.run(scenario())
    assert (denied.status, denied.reason, denied.replayed) == (S.DENIED, R.POLICY_DENIED, False)
    assert calls_after_denied == 1
    for again in (replay, later):
        assert (again.status, again.reason, again.replayed) == (S.DENIED, R.POLICY_DENIED, True)
        assert again.command_id == denied.command_id
    assert (fresh.status, fresh.replayed) == (S.VERIFIED, False)
    assert app.executor.calls == 2  # the denied run and the new key's run only
    assert system.desk.ticket_count == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "denied" and row["reason"] == "policy_denied"


def test_invalid_input_persists_failed_and_replays(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()
    blank = {"title": "   ", "description": "Something happened"}

    async def scenario():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            return app, await app.submit(key, blank), await app.submit(key, blank)

    app, first, again = asyncio.run(scenario())
    assert (first.status, first.reason, first.replayed) == (S.FAILED, R.INPUT_INVALID, False)
    assert (again.status, again.reason, again.replayed) == (S.FAILED, R.INPUT_INVALID, True)
    assert again.command_id == first.command_id and app.executor.calls == 1
    assert system.desk.ticket_count == 0
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "failed" and row["reason"] == "input_invalid"


def test_uncertain_write_persists_requires_human_and_never_writes_twice(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)

    async def scenario():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            first = await app.submit(key)
            events = list(app.sink.events)
            return app, first, events, await app.submit(key)

    app, first, events, again = asyncio.run(scenario())
    assert (first.status, first.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert first.execution_reference_id is None and first.audit_complete is True
    # The ticket physically exists (verification evidence found it), exactly once.
    assert system.desk.ticket_count == 1
    assert system.spy.correlation_reads == [first.action_run_id]
    assert events[-1].verification_code == "ticket_present"
    assert (again.status, again.reason, again.replayed) == (
        S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN, True,
    )  # fmt: skip
    assert app.executor.calls == 1 and len(system.spy.creates) == 1
    assert system.desk.ticket_count == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "requires_human" and row["reason"] == "execution_outcome_uncertain"


class FailingCompleteStore:
    """Fault injector: a real Postgres claim, then a terminal update that fails."""

    def __init__(self, inner: PostgresWriteCommandStore) -> None:
        self.inner = inner

    async def claim(self, claim):
        return await self.inner.claim(claim)

    async def get(self, command_id):
        return await self.inner.get(command_id)

    async def complete(self, command_id, outcome):
        raise sa.exc.OperationalError("UPDATE", {}, ConnectionError("SENSITIVE-DB-ERROR"))


def test_terminal_persistence_failure_never_leads_to_a_second_execution(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()

    async def failing_instance():
        async with product_store(migrated) as store:
            app = Instance.build(FailingCompleteStore(store), system)
            return app, await app.submit(key)

    app_a, result = asyncio.run(failing_instance())
    assert (result.status, result.reason, result.persistence_complete, result.replayed) == (
        S.REQUIRES_HUMAN, R.COMMAND_PERSISTENCE_INCOMPLETE, False, False,
    )  # fmt: skip
    assert app_a.executor.calls == 1
    assert system.desk.ticket_count == 1  # the effect happened
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "in_progress" and row["reason"] is None  # durable claim remains

    async def healthy_retry():
        async with product_store(migrated) as store:
            app = Instance.build(store, system)
            return app, await app.submit(key)

    app_b, retry = asyncio.run(healthy_retry())
    assert (retry.status, retry.reason, retry.replayed) == (S.IN_PROGRESS, None, True)
    assert retry.command_id == result.command_id
    assert app_b.executor.calls == 0 and app_b.sink.events == []
    assert system.desk.ticket_count == 1 and len(system.spy.creates) == 1
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "in_progress"


def test_execution_exception_persists_requires_human_without_text(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()

    async def scenario():
        async with product_store(migrated) as store:
            broken = Instance.build(store, system, raise_error=True)
            first = await broken.submit(key)
            healthy = Instance.build(store, system)
            return broken, healthy, first, await healthy.submit(key)

    broken, healthy, first, again = asyncio.run(scenario())
    assert (first.status, first.reason, first.persistence_complete) == (
        S.REQUIRES_HUMAN, R.COMMAND_EXECUTION_ERROR, True,
    )  # fmt: skip
    assert (again.status, again.reason, again.replayed) == (
        S.REQUIRES_HUMAN, R.COMMAND_EXECUTION_ERROR, True,
    )  # fmt: skip
    assert broken.executor.calls == 1 and healthy.executor.calls == 0
    (row,) = rows_for_key(engine, key)
    assert row["status"] == "requires_human" and row["reason"] == "command_execution_error"
    assert "SENSITIVE" not in json.dumps(row, default=str)
    assert "exploded" not in json.dumps(row, default=str)


def test_write_commands_store_no_raw_key_parameters_or_payloads(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()

    async def scenario():
        async with product_store(migrated) as store:
            return await Instance.build(store, system).submit(key)

    asyncio.run(scenario())
    columns = sa.inspect(engine).get_columns("write_commands", schema="product")
    names = {c["name"] for c in columns}
    forbidden = (
        "title", "description", "param", "payload", "prompt", "message", "permission", "role",
        "provider", "response", "event", "raw", "body", "json",
    )  # fmt: skip
    assert {n for n in names if any(f in n for f in forbidden)} == set()
    # The only key-derived column is the hash; there is no plaintext key column.
    assert {n for n in names if "key" in n} == {"idempotency_key_hash"}
    # No JSON/blob column could hold a payload.
    assert {type(c["type"]).__name__ for c in columns} <= {
        "UUID", "TEXT", "VARCHAR", "CHAR", "BOOLEAN", "TIMESTAMP",
    }  # fmt: skip

    (row,) = rows_for_key(engine, key)
    dump = json.dumps(row, default=str)
    for marker in (key, TITLE_MARKER, DESCRIPTION_MARKER, "SENSITIVEKEY", "Parcel delayed",
                   "No scan", "tickets.create"):  # fmt: skip
        assert marker not in dump
    assert row["idempotency_key_hash"] == hash_idempotency_key(key)
    # Also nowhere in the whole table (no other column or row carries them).
    with engine.connect() as connection:
        table_dump = connection.execute(
            sa.text("SELECT string_agg(t::text, '|') FROM product.write_commands t")
        ).scalar_one()
    for marker in (key, TITLE_MARKER, DESCRIPTION_MARKER):
        assert marker not in table_dump


class MutatingClaimStore:
    """Real Postgres claim; the caller's parameters are mutated while it is awaited."""

    def __init__(self, inner: PostgresWriteCommandStore, mutate) -> None:
        self.inner, self.mutate = inner, mutate
        self.claims = []

    async def claim(self, claim):
        self.claims.append(claim)
        self.mutate()
        return await self.inner.claim(claim)

    async def get(self, command_id):
        return await self.inner.get(command_id)

    async def complete(self, command_id, outcome):
        return await self.inner.complete(command_id, outcome)


def test_parameters_mutated_during_the_claim_are_never_executed(migrated, engine) -> None:
    key, system = new_key(), TicketSystem.build()
    original = {"title": "Ticket ORIGINAL", "description": "ORIGINAL description"}
    expected_fingerprint = request_fingerprint(
        action_name=ACTION, company_id=COMPANY, store_id=STORE, parameters=dict(original)
    )

    def mutate() -> None:
        original.update(title="Ticket MUTATED", description="MUTATED description")

    async def scenario():
        async with product_store(migrated) as store:
            racing = MutatingClaimStore(store, mutate)
            app = Instance.build(racing, system)
            return racing, await app.submit(key, original)

    racing, result = asyncio.run(scenario())
    assert original["title"] == "Ticket MUTATED"  # the caller really mutated
    assert result.status is S.VERIFIED
    (call,) = system.spy.creates
    assert (call["title"], call["description"]) == ("Ticket ORIGINAL", "ORIGINAL description")
    ticket = asyncio.run(system.spy.get_ticket(UUID(result.execution_reference_id)))
    assert (ticket.title, ticket.description) == ("Ticket ORIGINAL", "ORIGINAL description")
    (claim,) = racing.claims
    assert claim.request_fingerprint == expected_fingerprint
    (row,) = rows_for_key(engine, key)
    assert row["request_fingerprint"] == expected_fingerprint
    assert "MUTATED" not in json.dumps(row, default=str)
    assert "ORIGINAL" not in json.dumps(row, default=str)
