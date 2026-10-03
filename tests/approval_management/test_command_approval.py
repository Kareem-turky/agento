"""WriteCommand continuation after a human approval (Task 036): the awaiting state is
durable and resumable once, by resubmitting the SAME request (same key, parameters and
trusted scope) with the approval id it awaits. No parameters are persisted."""

import asyncio
from uuid import uuid4

import pytest

from app.commands import CommandReason, CommandStatus, WriteCommandCoordinator
from app.commands.errors import (
    ApprovalContinuationRefusedError,
    IdempotencyConflictError,
    WriteCommandStoreError,
)
from app.governance import ActionIntent, ActionScope
from tests.commands.fakes import InMemoryWriteCommandStore
from tests.support.approval_fakes import (
    APPROVER,
    BUDGET_UPDATE,
    COMPANY,
    REQUESTER,
    STORE_A,
    ApprovalWorld,
    actor,
    request,
)

KEY = "budget-change-0001"
PARAMS = {"campaign": "spring", "amount": 150, "reason": "Spring sale"}
REQ = actor("requester-1", REQUESTER)
APP = actor("approver-1", APPROVER)


def run(coroutine):
    return asyncio.run(coroutine)


class CountingExecutor:
    """Counts ExecutionCoordinator calls (to prove a refused caller never reaches it)."""

    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, []

    async def run(self, request, *args, **kwargs):
        self.calls.append(request.actor.actor_type)
        return await self.inner.run(request, *args, **kwargs)


class Commands:
    def __init__(self, *, guard: bool = True) -> None:
        self.world = ApprovalWorld()
        self.store = InMemoryWriteCommandStore()
        self.executor = CountingExecutor(self.world.coordinator)
        self.commands = WriteCommandCoordinator(
            self.store, self.executor, self.world.catalog,  # type: ignore[arg-type]
            approvals=self.world.broker if guard else None,
        )  # fmt: skip

    def submit(self, params=None, *, key=KEY, approval_id=None, who=REQ):
        return self.commands.submit(
            request(who), ActionScope(company_id=COMPANY, store_id=STORE_A),
            ActionIntent(name=BUDGET_UPDATE.name), params or PARAMS, key,
            approval_id=approval_id,
        )  # fmt: skip


def test_awaiting_command_continues_once_after_approval() -> None:
    env = Commands()
    first = run(env.submit())
    assert (first.status, first.reason) == (CommandStatus.AWAITING_APPROVAL,
                                            CommandReason.APPROVAL_REQUIRED)  # fmt: skip
    assert first.approval_id is not None and env.world.budget.effects == []
    stored = env.store.rows[first.command_id]
    assert stored["approval_id"] == first.approval_id
    # Raw parameters are never persisted: only hashes and metadata.
    assert "amount" not in repr(stored) and "Spring sale" not in repr(stored)
    approval = env.world.repository.rows[first.approval_id]
    assert approval.source.kind.value == "write_command"
    assert approval.source.command_id == first.command_id
    # Replay without an approval id: the awaiting state, nothing runs.
    again = run(env.submit())
    assert again.replayed and again.status is CommandStatus.AWAITING_APPROVAL
    run(env.world.service.approve(request(APP), first.approval_id, None))
    done = run(env.submit(approval_id=first.approval_id))
    assert (done.status, done.reason, done.replayed) == (CommandStatus.VERIFIED,
                                                         CommandReason.VERIFIED, False)  # fmt: skip
    assert done.command_id == first.command_id and done.approval_id == first.approval_id
    assert env.world.budget.effects == [(STORE_A, "spring", 150)]
    # Terminal replay never executes again (with or without the approval id).
    for approval_id in (None, first.approval_id):
        replay = run(env.submit(approval_id=approval_id))
        assert replay.replayed and replay.status is CommandStatus.VERIFIED
    assert len(env.world.budget.effects) == 1


def test_wrong_approval_id_changes_nothing() -> None:
    env = Commands()
    first = run(env.submit())
    with pytest.raises(ApprovalContinuationRefusedError):
        run(env.submit(approval_id=uuid4()))
    assert env.store.rows[first.command_id]["status"] is CommandStatus.AWAITING_APPROVAL
    assert env.world.budget.effects == []


def test_different_parameters_conflict_before_anything_runs() -> None:
    env = Commands()
    first = run(env.submit())
    run(env.world.service.approve(request(APP), first.approval_id, None))
    with pytest.raises(IdempotencyConflictError):
        run(env.submit({**PARAMS, "amount": 151}, approval_id=first.approval_id))
    assert env.world.budget.effects == []
    assert env.world.repository.rows[first.approval_id].consumed_at is None


def test_rejected_approval_ends_the_command_safely() -> None:
    env = Commands()
    first = run(env.submit())
    run(env.world.service.reject(request(APP), first.approval_id, "not now"))
    done = run(env.submit(approval_id=first.approval_id))
    assert (done.status, done.reason) == (CommandStatus.FAILED, CommandReason.APPROVAL_REJECTED)
    assert env.world.budget.effects == []
    assert run(env.submit(approval_id=first.approval_id)).replayed


def test_concurrent_continuation_executes_once() -> None:
    env = Commands()
    first = run(env.submit())
    run(env.world.service.approve(request(APP), first.approval_id, None))

    async def race():
        return await asyncio.gather(*(env.submit(approval_id=first.approval_id)
                                      for _ in range(10)))  # fmt: skip

    results = run(race())
    assert [r.replayed for r in results].count(False) == 1
    assert len(env.world.budget.effects) == 1
    assert len([r for r in env.world.repository.history
                if r.event_type.value == "execution_claimed"]) == 1  # fmt: skip


def test_the_ticket_style_low_risk_write_never_creates_an_approval() -> None:
    from app.operations import OPERATIONS_ACTIONS

    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk.value == "low_risk_write"


# ----- the exact requester principal owns an approval-linked command ---------------------------

PRINCIPAL = actor("same-principal", REQUESTER)  # actor_type "user"
TWIN = actor("same-principal", REQUESTER, actor_type="api_client")


def awaiting_for(env: Commands, who=PRINCIPAL):
    first = run(env.submit(who=who))
    assert first.status is CommandStatus.AWAITING_APPROVAL and first.approval_id is not None
    return first


def snapshot(env: Commands, first):
    return (
        dict(env.store.rows[first.command_id]),
        env.world.repository.rows[first.approval_id],
        len(env.world.repository.history),
    )


def test_wrong_actor_type_cannot_poison_a_waiting_command() -> None:
    env = Commands()
    first = awaiting_for(env)
    run(env.world.service.approve(request(APP), first.approval_id, None))
    before, calls = snapshot(env, first), len(env.executor.calls)
    with pytest.raises(ApprovalContinuationRefusedError) as info:
        run(env.submit(approval_id=first.approval_id, who=TWIN))
    assert str(first.approval_id) not in str(info.value)
    # Nothing changed: command, approval (unconsumed), approval history, effects, and the
    # ExecutionCoordinator was never reached after the precheck.
    assert snapshot(env, first) == before
    assert env.store.rows[first.command_id]["status"] is CommandStatus.AWAITING_APPROVAL
    assert env.world.repository.rows[first.approval_id].consumed_at is None
    assert env.world.budget.effects == [] and len(env.executor.calls) == calls
    # The real requester still continues it, exactly once.
    done = run(env.submit(approval_id=first.approval_id, who=PRINCIPAL))
    assert (done.status, done.replayed) == (CommandStatus.VERIFIED, False)
    assert env.world.budget.effects == [(STORE_A, "spring", 150)]


def test_wrong_actor_type_cannot_inspect_an_approval_linked_replay() -> None:
    env = Commands()
    first = awaiting_for(env)
    before = snapshot(env, first)
    with pytest.raises(ApprovalContinuationRefusedError) as info:
        run(env.submit(who=TWIN))  # no approval id: a plain replay attempt
    assert str(first.approval_id) not in repr(info.value) and info.value.args == (
        "approval_continuation_refused",
    )
    assert snapshot(env, first) == before
    # The requester's own plain replay is unchanged.
    again = run(env.submit(who=PRINCIPAL))
    assert again.replayed and again.approval_id == first.approval_id


def test_terminal_approval_linked_replay_is_only_for_the_requester() -> None:
    env = Commands()
    first = awaiting_for(env)
    run(env.world.service.approve(request(APP), first.approval_id, None))
    done = run(env.submit(approval_id=first.approval_id, who=PRINCIPAL))
    assert done.status is CommandStatus.VERIFIED
    calls = len(env.executor.calls)
    for approval_id in (None, first.approval_id):
        with pytest.raises(ApprovalContinuationRefusedError):
            run(env.submit(approval_id=approval_id, who=TWIN))
    assert len(env.executor.calls) == calls and len(env.world.budget.effects) == 1
    replay = run(env.submit(who=PRINCIPAL))
    assert replay.replayed and replay.status is CommandStatus.VERIFIED
    assert replay.approval_id == first.approval_id


def test_concurrent_wrong_and_right_principals() -> None:
    env = Commands()
    first = awaiting_for(env)
    run(env.world.service.approve(request(APP), first.approval_id, None))
    env.executor.calls.clear()  # only count the race

    async def race():
        calls = [env.submit(approval_id=first.approval_id, who=TWIN) for _ in range(5)]
        calls += [env.submit(approval_id=first.approval_id, who=PRINCIPAL) for _ in range(5)]
        return await asyncio.gather(*calls, return_exceptions=True)

    outcomes = run(race())
    assert all(isinstance(o, ApprovalContinuationRefusedError) for o in outcomes[:5])
    right = outcomes[5:]
    assert not any(isinstance(o, Exception) for o in right)
    assert [o.replayed for o in right].count(False) == 1
    assert env.executor.calls == ["user"]  # only the exact principal ever executes
    assert len(env.world.budget.effects) == 1
    assert len([e for e in env.world.repository.history
                if e.event_type.value == "execution_claimed"]) == 1  # fmt: skip


def test_the_guard_is_mandatory_and_fails_closed() -> None:
    env = Commands(guard=False)
    awaiting_for(env)
    with pytest.raises(WriteCommandStoreError):  # no guard: never returned or reopened
        run(env.submit(who=PRINCIPAL))
    env2 = Commands()
    first2 = awaiting_for(env2)
    before = snapshot(env2, first2)
    env2.world.repository.fail = True
    with pytest.raises(WriteCommandStoreError):
        run(env2.submit(approval_id=first2.approval_id, who=PRINCIPAL))
    env2.world.repository.fail = False
    assert snapshot(env2, first2) == before
    assert env.world.budget.effects == [] and env2.world.budget.effects == []


def test_the_guard_never_consumes_and_checks_source_and_company() -> None:
    env = Commands()
    first = awaiting_for(env)
    run(env.world.service.approve(request(APP), first.approval_id, None))
    broker, history = env.world.broker, len(env.world.repository.history)

    def owns(**kw):
        base = dict(company_id=COMPANY, approval_id=first.approval_id,
                    requester_actor_id="same-principal", requester_actor_type="user",
                    command_id=first.command_id)  # fmt: skip
        args = {**base, **kw}
        return run(broker.is_command_requester(
            args.pop("company_id"), args.pop("approval_id"), **args))  # fmt: skip

    assert owns() is True
    for change in (dict(requester_actor_type="api_client"),
                   dict(requester_actor_type="system_agent"),
                   dict(requester_actor_id="other"), dict(command_id=uuid4()),
                   dict(company_id="00000000-0000-4000-8000-0000000000c2"),
                   dict(approval_id=uuid4())):  # fmt: skip
        assert owns(**change) is False, change
    assert len(env.world.repository.history) == history  # read only: no event
    assert env.world.repository.rows[first.approval_id].consumed_at is None
    # An action-sourced request (not this command's) never passes either.
    direct = run(env.world.run_budget(PRINCIPAL, campaign="other"))
    assert run(broker.is_command_requester(
        COMPANY, direct.approval_id, requester_actor_id="same-principal",
        requester_actor_type="user", command_id=first.command_id)) is False  # fmt: skip
