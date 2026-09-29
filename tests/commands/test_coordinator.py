"""WriteCommandCoordinator with the real ExecutionCoordinator and an in-memory store.

The in-memory store is a test fake; durability and concurrency are proven against
PostgreSQL in tests/integration.
"""

from collections.abc import Mapping
from uuid import UUID

import pytest

from app.commands import (
    AnonymousWriteCommandError,
    CommandReason,
    CommandStatus,
    IdempotencyConflictError,
    InvalidCommandParametersError,
    InvalidIdempotencyKeyError,
    ReadActionNotAllowedError,
    UnknownWriteActionError,
    WriteCommandCoordinator,
    WriteCommandStoreError,
    hash_idempotency_key,
    request_fingerprint,
)
from app.context.models import RequestContext
from app.execution import ActionHandlerRegistry
from app.governance import ActionIntent, GovernanceGate
from tests.commands.fakes import CountingExecutionCoordinator, InMemoryWriteCommandStore
from tests.execution.fakes import (
    CATALOG,
    FIXED_TIME,
    VALID_PARAMS,
    FakeHandler,
    RecordingAuditSink,
    actor,
    request,
    run,
    store_scope,
)

KEY = "3f1c3b9e-8c1e-4c7a-9b2d-6f0e1a2b3c4d"
S, R = CommandStatus, CommandReason


class Env:
    def __init__(self, store=None, handler=None, *, raise_error=False) -> None:
        self.store = store or InMemoryWriteCommandStore()
        self.handler = handler or FakeHandler()
        self.sink = RecordingAuditSink()
        self.executor = CountingExecutionCoordinator(
            GovernanceGate(CATALOG), ActionHandlerRegistry([self.handler]), self.sink,
            clock=lambda: FIXED_TIME, raise_error=raise_error,
        )  # fmt: skip
        self.commands = WriteCommandCoordinator(self.store, self.executor, CATALOG)

    def submit(self, *, name="notes.add", params=None, key=KEY, req=None, scope=None):
        return run(
            self.commands.submit(
                req if req is not None else request(),
                scope if scope is not None else store_scope(),
                ActionIntent(name=name),
                VALID_PARAMS if params is None else params,
                key,
            )
        )


def test_new_command_executes_once_and_persists_verified() -> None:
    env = Env()
    result = env.submit()
    assert (result.status, result.reason, result.replayed, result.persistence_complete) == (
        S.VERIFIED, R.VERIFIED, False, True,
    )  # fmt: skip
    assert result.execution_reference_id == "note-123" and result.audit_complete is True
    assert env.executor.calls == 1 and len(env.handler.execute_calls) == 1
    (claim,) = env.store.claims
    assert claim.command_id == result.command_id
    assert claim.idempotency_key_hash == hash_idempotency_key(KEY)
    assert (claim.company_id, claim.actor_id, claim.store_id) == ("company-1", "user-1", "store-a")
    stored = run(env.store.get(result.command_id))
    assert stored.status is S.VERIFIED and stored.action_run_id == result.action_run_id
    assert env.sink.events[0].run_id == result.action_run_id


def test_the_claim_is_made_before_execution() -> None:
    env = Env()
    seen: list[CommandStatus] = []
    original = env.handler.execute

    async def execute(context, validated):
        (row,) = env.store.rows.values()
        seen.append(row["status"])
        return await original(context, validated)

    env.handler.execute = execute  # type: ignore[method-assign]
    env.submit()
    assert seen == [S.IN_PROGRESS]


def test_same_key_same_request_replays_without_executing() -> None:
    env = Env()
    first = env.submit()
    events = len(env.sink.events)
    second = env.submit(params={"text": "Customer called", "order_ref": "ord-1"})
    assert second.replayed is True and second.persistence_complete is True
    assert second.model_dump(exclude={"replayed"}) == first.model_dump(exclude={"replayed"})
    assert env.executor.calls == 1 and len(env.handler.execute_calls) == 1
    assert len(env.sink.events) == events and len(env.store.rows) == 1


def test_same_key_different_request_conflicts_without_executing() -> None:
    env = Env()
    env.submit()
    for kwargs in (
        {"params": {"order_ref": "ord-1", "text": "Other text"}},
        {"scope": store_scope("store-b")},
        {"name": "orders.cancel"},
    ):
        with pytest.raises(IdempotencyConflictError) as info:
            env.submit(**kwargs)
        assert str(info.value) == "idempotency_conflict"
        assert KEY not in repr(info.value)
    assert env.executor.calls == 1 and len(env.store.rows) == 1


def test_namespaces_are_per_company_and_actor() -> None:
    env = Env()
    env.submit()
    other_actor = request(actor(actor_id="user-2"))
    assert env.submit(req=other_actor).replayed is False
    assert env.submit(key="another-key").replayed is False
    assert env.executor.calls == 3 and len(env.store.rows) == 3


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"req": RequestContext()}, AnonymousWriteCommandError),
        ({"name": "notes.read"}, ReadActionNotAllowedError),
        ({"name": "unknown.action"}, UnknownWriteActionError),
        ({"key": ""}, InvalidIdempotencyKeyError),
        ({"key": "has space"}, InvalidIdempotencyKeyError),
        ({"key": "k" * 129}, InvalidIdempotencyKeyError),
        ({"key": None}, InvalidIdempotencyKeyError),
        ({"params": {"order_ref": "o", "text": float("nan")}}, InvalidCommandParametersError),
        ({"params": {"order_ref": ("tuple",), "text": "t"}}, InvalidCommandParametersError),
    ],
)
def test_rejections_happen_before_any_claim_or_execution(kwargs, error) -> None:
    env = Env()
    with pytest.raises(error):
        env.submit(**kwargs)
    assert env.store.claims == [] and env.executor.calls == 0 and env.sink.events == []


def test_claim_failure_raises_a_safe_store_error_and_executes_nothing() -> None:
    env = Env(InMemoryWriteCommandStore(fail_claim=True))
    with pytest.raises(WriteCommandStoreError) as info:
        env.submit()
    assert "SENSITIVE" not in repr(info.value) and info.value.__cause__ is None
    assert env.executor.calls == 0


def test_invalid_claim_result_fails_closed() -> None:
    env = Env()

    async def bad_claim(claim):
        return {"outcome": "new"}

    env.store.claim = bad_claim  # type: ignore[method-assign]
    with pytest.raises(WriteCommandStoreError):
        env.submit()
    assert env.executor.calls == 0


@pytest.mark.parametrize(
    ("handler", "status", "reason"),
    [
        (FakeHandler(execute_behaviour="no_effect"), S.FAILED, R.EXECUTION_FAILED_NO_EFFECT),
        (FakeHandler(execute_behaviour="uncertain"), S.REQUIRES_HUMAN,
         R.EXECUTION_OUTCOME_UNCERTAIN),
        (FakeHandler(verify_behaviour="mismatch"), S.REQUIRES_HUMAN, R.VERIFICATION_FAILED),
    ],
)  # fmt: skip
def test_terminal_run_outcomes_are_persisted_and_replayed(handler, status, reason) -> None:
    env = Env(handler=handler)
    first = env.submit()
    assert (first.status, first.reason, first.replayed) == (status, reason, False)
    second = env.submit()
    assert (second.status, second.reason, second.replayed) == (status, reason, True)
    assert env.executor.calls == 1


def test_denied_and_awaiting_approval_are_persisted_and_replayed() -> None:
    env = Env(handler=FakeHandler("orders.cancel"))
    denied = env.submit(req=request(actor(permissions=frozenset())))
    assert (denied.status, denied.reason) == (S.DENIED, R.POLICY_DENIED)
    approval = env.submit(name="orders.cancel", key="key-2")
    assert (approval.status, approval.reason) == (S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED)
    assert env.submit(name="orders.cancel", key="key-2").replayed is True
    assert env.executor.calls == 2


def test_execution_exception_persists_requires_human_without_text() -> None:
    env = Env(raise_error=True)
    result = env.submit()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.COMMAND_EXECUTION_ERROR)
    assert result.persistence_complete is True and result.action_run_id is None
    (outcome,) = env.store.completions
    assert "SENSITIVE" not in repr(outcome) and "exploded" not in repr(outcome)
    again = env.submit()
    assert (again.status, again.reason, again.replayed) == (
        S.REQUIRES_HUMAN, R.COMMAND_EXECUTION_ERROR, True,
    )  # fmt: skip
    assert env.executor.calls == 1


def test_invalid_action_run_is_treated_as_an_execution_error() -> None:
    env = Env()

    async def bogus(*args, **kwargs):
        env.executor.calls += 1
        return {"status": "verified"}

    env.executor.run = bogus  # type: ignore[method-assign]
    result = env.submit()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.COMMAND_EXECUTION_ERROR)


def test_terminal_persistence_failure_returns_incomplete_and_keeps_in_progress() -> None:
    env = Env(InMemoryWriteCommandStore(fail_complete=True))
    result = env.submit()
    assert (result.status, result.reason, result.persistence_complete, result.replayed) == (
        S.REQUIRES_HUMAN, R.COMMAND_PERSISTENCE_INCOMPLETE, False, False,
    )  # fmt: skip
    assert isinstance(result.action_run_id, UUID)
    (row,) = env.store.rows.values()
    assert row["status"] is S.IN_PROGRESS
    env.store.fail_complete = False  # healthy store now
    retry = env.submit()
    assert (retry.status, retry.reason, retry.replayed) == (S.IN_PROGRESS, None, True)
    assert retry.command_id == result.command_id and env.executor.calls == 1


def test_mismatching_completion_record_is_treated_as_persistence_incomplete() -> None:
    env = Env()
    original = env.store.complete

    async def complete(command_id, outcome):
        stored = await original(command_id, outcome)
        return stored.model_copy(update={"status": S.FAILED, "reason": R.INPUT_INVALID})

    env.store.complete = complete  # type: ignore[method-assign]
    result = env.submit()
    assert (result.reason, result.persistence_complete) == (
        R.COMMAND_PERSISTENCE_INCOMPLETE, False,
    )  # fmt: skip


def test_in_progress_replay_never_executes() -> None:
    env = Env()
    env.store.fail_complete = True
    env.submit()
    env.store.fail_complete = False
    for _ in range(3):
        assert env.submit().status is S.IN_PROGRESS
    assert env.executor.calls == 1


def test_a_new_claim_must_be_the_command_just_created() -> None:
    env = Env()
    original = env.store.claim

    async def claim(c):
        result = await original(c)
        record = result.record.model_copy(update={"command_id": UUID(int=1)})
        return result.model_copy(update={"record": record})

    env.store.claim = claim  # type: ignore[method-assign]
    with pytest.raises(WriteCommandStoreError):
        env.submit()
    assert env.executor.calls == 0


# --- The fingerprinted request must be the exact request executed (TOCTOU) ---------


class MutatingClaimStore(InMemoryWriteCommandStore):
    """Mutates the CALLER's parameters while the claim is awaited (the race)."""

    def __init__(self, mutate) -> None:
        super().__init__()
        self.mutate = mutate

    async def claim(self, claim):
        self.mutate()
        return await super().claim(claim)


def executed_fingerprint(handler: FakeHandler, params: dict) -> str:
    return request_fingerprint(
        action_name="notes.add", company_id="company-1", store_id="store-a", parameters=params
    )


def test_caller_mutation_during_the_claim_never_reaches_execution() -> None:
    original = {"order_ref": "ord-1", "text": "ORIGINAL"}
    expected = executed_fingerprint(FakeHandler(), dict(original))
    store = MutatingClaimStore(lambda: original.update(text="MUTATED"))
    env = Env(store)
    result = env.submit(params=original)

    assert original["text"] == "MUTATED"  # the caller really mutated its dict
    assert result.status is S.VERIFIED and env.executor.calls == 1
    (validated,) = env.handler.execute_calls
    assert validated.text == "ORIGINAL"
    (raw,) = env.handler.validate_calls
    assert dict(raw) == {"order_ref": "ord-1", "text": "ORIGINAL"}
    (claim,) = store.claims
    assert claim.request_fingerprint == expected
    # The executed parameters are provably the fingerprinted ones.
    assert executed_fingerprint(env.handler, dict(raw)) == claim.request_fingerprint


def test_executed_parameters_are_detached_from_the_caller() -> None:
    original = {"order_ref": "ord-1", "text": "ORIGINAL"}
    env = Env()
    env.submit(params=original)
    (raw,) = env.handler.validate_calls
    original["text"] = "LATER"
    assert raw["text"] == "ORIGINAL"


class ShiftingMapping(Mapping):
    """Hostile Mapping: every value read returns a new version."""

    def __init__(self) -> None:
        self.reads = 0

    def __getitem__(self, key):
        if key not in ("order_ref", "text"):
            raise KeyError(key)
        self.reads += 1
        return f"{key}-v{self.reads}"

    def __iter__(self):
        return iter(("order_ref", "text"))

    def __len__(self) -> int:
        return 2


def test_hostile_mapping_is_materialized_once_for_fingerprint_and_execution() -> None:
    hostile = ShiftingMapping()
    env = Env()
    env.submit(params=hostile)
    reads_at_end = hostile.reads
    (claim,) = env.store.claims
    (raw,) = env.handler.validate_calls
    executed = dict(raw)
    assert executed_fingerprint(env.handler, executed) == claim.request_fingerprint
    assert reads_at_end == 2  # each value read exactly once: one snapshot
    (validated,) = env.handler.execute_calls
    assert (validated.order_ref, validated.text) == (executed["order_ref"], executed["text"])


def test_nested_caller_mutation_during_the_claim_never_reaches_execution() -> None:
    original = {
        "order_ref": "ord-1",
        "text": "t",
        "payload": {"items": [{"value": "ORIGINAL"}]},
    }
    store = MutatingClaimStore(
        lambda: original["payload"]["items"][0].update(value="MUTATED")  # type: ignore[index]
    )
    env = Env(store)
    env.submit(params=original)  # NoteInput rejects "payload": FAILED input_invalid
    (raw,) = env.handler.validate_calls
    assert raw["payload"]["items"][0]["value"] == "ORIGINAL"
    (claim,) = store.claims
    assert executed_fingerprint(env.handler, dict(raw)) == claim.request_fingerprint
