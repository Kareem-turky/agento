"""WriteCommand continuation after a human approval (Task 036): the awaiting state is
durable and resumable once, by resubmitting the SAME request (same key, parameters and
trusted scope) with the approval id it awaits. No parameters are persisted."""

import asyncio
from uuid import uuid4

import pytest

from app.commands import CommandReason, CommandStatus, WriteCommandCoordinator
from app.commands.errors import ApprovalContinuationRefusedError, IdempotencyConflictError
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


class Commands:
    def __init__(self) -> None:
        self.world = ApprovalWorld()
        self.store = InMemoryWriteCommandStore()
        self.commands = WriteCommandCoordinator(self.store, self.world.coordinator,
                                                self.world.catalog)  # fmt: skip

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


def test_same_actor_id_under_another_actor_type_never_continues_the_command() -> None:
    """WriteCommand idempotency is scoped by (company, actor id, key) since Task 013 (no
    actor type is stored), so a same-id twin of another actor type replays the command.
    Its continuation still never executes: the approval binds to the exact principal, so
    the claim is a mismatch, the approval stays unconsumed and nothing runs. The command
    ends failed/approval_mismatch (fail-closed, no effect)."""
    env = Commands()
    first = run(env.submit())
    run(env.world.service.approve(request(APP), first.approval_id, None))
    twin = actor("requester-1", REQUESTER, actor_type="api_client")
    refused = run(env.submit(approval_id=first.approval_id, who=twin))
    assert (refused.status, refused.reason) == (CommandStatus.FAILED,
                                                CommandReason.APPROVAL_MISMATCH)  # fmt: skip
    assert env.world.budget.effects == []
    assert env.world.repository.rows[first.approval_id].consumed_at is None
    # Terminal: nothing ever executes for this command again, for anyone.
    assert run(env.submit(approval_id=first.approval_id)).replayed
    assert env.world.budget.effects == []
