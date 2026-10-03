"""ExecutionCoordinator + human approval (Task 036), with the real ProductApprovalBroker,
governance and ApprovalService over the in-memory approval repository (PostgreSQL is
proven in tests/integration/test_approvals_postgres.py). TEST-ONLY MEDIUM/HIGH actions:
no real high-risk business action exists. No model, no network."""

import asyncio
import socket
from uuid import uuid4

import pytest

from app.approval_management.errors import ApprovalSelfDecisionError
from app.approval_management.state import ApprovalEventType, ApprovalStatus
from app.execution import ActionRunReason as R
from app.execution import ActionRunStatus as S
from app.execution import AuditEventType as E
from app.governance import ActionIntent, ActionScope
from tests.support.approval_fakes import (
    APPROVER,
    COMPANY,
    OTHER_COMPANY,
    PAYOUT_RELEASE,
    REQUESTER,
    STORE_A,
    STORE_B,
    UNDESCRIBED,
    ApprovalWorld,
    actor,
    request,
)


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


REQ = actor("requester-1", REQUESTER, stores=frozenset({STORE_A, STORE_B}))
APP = actor("approver-1", APPROVER, stores=frozenset({STORE_A, STORE_B}))


def requested(world: ApprovalWorld, who=REQ, **kw):
    first = run(world.run_budget(who, **kw))
    assert (first.status, first.reason) == (S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED)
    assert first.approval_id is not None
    return first


def approved(world: ApprovalWorld, **kw):
    first = requested(world, **kw)
    run(world.service.approve(request(APP), first.approval_id, "looks right"))
    return first


def test_full_direct_flow_executes_exactly_once() -> None:
    world = ApprovalWorld()
    first = requested(world)
    # Nothing executed; the safe summary carries the before/after change.
    assert world.budget.effects == [] and first.execution_result is None
    stored = world.repository.rows[first.approval_id]
    assert stored.status is ApprovalStatus.REQUESTED and stored.risk.value == "medium_risk"
    (change,) = stored.summary.changes
    assert (change.code, change.before, change.after) == ("budget", "100", "150")
    assert stored.source.kind.value == "action"
    # The requester cannot approve their own request, even holding approvals.decide.
    self_decider = actor("requester-1", REQUESTER | APPROVER, stores=REQ.store_ids)
    with pytest.raises(ApprovalSelfDecisionError):
        run(world.service.approve(request(self_decider), first.approval_id, None))
    assert world.repository.rows[first.approval_id].status is ApprovalStatus.REQUESTED
    # Another authorized human approves; the requester resubmits the exact action.
    run(world.service.approve(request(APP), first.approval_id, "within budget policy"))
    second = run(world.run_budget(REQ, approval_id=first.approval_id))
    assert (second.status, second.reason) == (S.VERIFIED, R.VERIFIED)
    assert second.approval_id == first.approval_id and second.audit_complete
    assert world.budget.effects == [(STORE_A, "spring", 150)]
    consumed = world.repository.rows[first.approval_id]
    assert consumed.status is ApprovalStatus.APPROVED  # the decision state never changes
    assert consumed.consumed_by_action_run_id == second.run_id
    assert consumed.execution_outcome.value == "verified"
    events = [e.event_type for e in world.repository.history
              if e.approval_id == first.approval_id]  # fmt: skip
    assert events == [ApprovalEventType.REQUESTED, ApprovalEventType.APPROVED,
                      ApprovalEventType.EXECUTION_CLAIMED,
                      ApprovalEventType.EXECUTION_COMPLETED]  # fmt: skip
    # The approval id is correlated in the action audit of BOTH runs (from the moment it
    # exists / is presented: the request's AWAITING event, the execution's events).
    correlated = {E.AWAITING_APPROVAL, E.EXECUTION_STARTED, E.EXECUTION_COMPLETED, E.VERIFIED}
    assert {e.approval_id for e in world.audit.events if e.event_type in correlated} == {
        first.approval_id
    }
    # Reuse never executes again.
    third = run(world.run_budget(REQ, approval_id=first.approval_id))
    assert (third.status, third.reason) == (S.FAILED, R.APPROVAL_ALREADY_CONSUMED)
    assert len(world.budget.effects) == 1


def test_high_risk_action_also_needs_a_decision() -> None:
    world = ApprovalWorld()
    scope = ActionScope(company_id=COMPANY)
    params = {"payout": "p-1", "amount": 900}
    first = run(world.coordinator.run(request(REQ), ActionIntent(name=PAYOUT_RELEASE.name),
                                      scope, params))  # fmt: skip
    assert first.status is S.AWAITING_APPROVAL and world.payout.released == []
    assert world.repository.rows[first.approval_id].risk.value == "high_risk"
    run(world.service.approve(request(APP), first.approval_id, None))
    done = run(world.coordinator.run(request(REQ), ActionIntent(name=PAYOUT_RELEASE.name),
                                     scope, params, approval_id=first.approval_id))  # fmt: skip
    assert done.status is S.VERIFIED and world.payout.released == [("p-1", 900)]


def test_parameter_mismatch_never_executes() -> None:
    world = ApprovalWorld()
    first = approved(world, amount=100)
    other = run(world.run_budget(REQ, amount=101, approval_id=first.approval_id))
    assert (other.status, other.reason) == (S.FAILED, R.APPROVAL_MISMATCH)
    assert world.budget.effects == []
    # The approval is untouched: the exact request can still use it.
    assert world.repository.rows[first.approval_id].consumed_at is None
    assert run(world.run_budget(REQ, amount=100, approval_id=first.approval_id)).status is (
        S.VERIFIED
    )


def test_store_mismatch_never_executes() -> None:
    world = ApprovalWorld()
    first = approved(world, store=STORE_A)
    other = run(world.run_budget(REQ, store=STORE_B, approval_id=first.approval_id))
    assert (other.status, other.reason) == (S.FAILED, R.APPROVAL_MISMATCH)
    assert world.budget.effects == []


def test_actor_mismatch_never_executes() -> None:
    world = ApprovalWorld()
    first = approved(world)
    colleague = actor("requester-2", REQUESTER, stores=REQ.store_ids)
    other = run(world.run_budget(colleague, approval_id=first.approval_id))
    assert (other.status, other.reason) == (S.FAILED, R.APPROVAL_MISMATCH)
    # The approver is not the requester either: an approval never makes them one.
    as_approver = actor("approver-1", APPROVER | REQUESTER, stores=REQ.store_ids)
    assert run(world.run_budget(as_approver, approval_id=first.approval_id)).reason is (
        R.APPROVAL_MISMATCH
    )
    assert world.budget.effects == []


def test_revoked_permission_is_denied_despite_an_approval() -> None:
    world = ApprovalWorld()
    first = approved(world)
    revoked = actor("requester-1", REQUESTER - {"budgets.update"}, stores=REQ.store_ids)
    result = run(world.run_budget(revoked, approval_id=first.approval_id))
    assert (result.status, result.reason) == (S.DENIED, R.POLICY_DENIED)
    assert world.budget.effects == []
    assert world.repository.rows[first.approval_id].consumed_at is None  # never claimed


def test_rejected_expired_and_cancelled_never_execute() -> None:
    world = ApprovalWorld()
    rejected = requested(world, campaign="a")
    run(world.service.reject(request(APP), rejected.approval_id, "not this quarter"))
    assert run(world.run_budget(REQ, campaign="a", approval_id=rejected.approval_id)).reason is (
        R.APPROVAL_REJECTED
    )
    cancelled = requested(world, campaign="b")
    run(world.service.cancel(request(REQ), cancelled.approval_id, "no longer needed"))
    assert run(world.run_budget(REQ, campaign="b", approval_id=cancelled.approval_id)).reason is (
        R.APPROVAL_CANCELLED
    )
    pending = requested(world, campaign="c")
    assert run(world.run_budget(REQ, campaign="c", approval_id=pending.approval_id)).reason is (
        R.APPROVAL_NOT_DECIDED
    )
    world.clock.advance(hours=25)  # past the Product-owned 24h TTL
    expired = run(world.run_budget(REQ, campaign="c", approval_id=pending.approval_id))
    assert (expired.status, expired.reason) == (S.FAILED, R.APPROVAL_EXPIRED)
    assert world.repository.rows[pending.approval_id].status is ApprovalStatus.EXPIRED
    assert world.budget.effects == []
    # A refusal never creates a replacement request.
    assert len(world.repository.rows) == 3


def test_an_approved_request_past_its_expiry_cannot_execute() -> None:
    world = ApprovalWorld()
    first = approved(world)
    world.clock.advance(hours=24)
    result = run(world.run_budget(REQ, approval_id=first.approval_id))
    assert (result.status, result.reason) == (S.FAILED, R.APPROVAL_EXPIRED)
    assert world.repository.rows[first.approval_id].status is ApprovalStatus.APPROVED
    assert world.budget.effects == []


def test_unknown_and_foreign_ids_are_indistinguishable() -> None:
    world = ApprovalWorld()
    first = approved(world)
    foreign = actor("requester-1", REQUESTER, company=OTHER_COMPANY, stores=REQ.store_ids)
    for who, approval_id in ((foreign, first.approval_id), (REQ, uuid4())):
        result = run(world.run_budget(who, approval_id=approval_id))
        assert (result.status, result.reason) == (S.FAILED, R.APPROVAL_NOT_FOUND)
    assert world.budget.effects == []


def test_missing_summary_or_persistence_fails_closed_without_an_id() -> None:
    world = ApprovalWorld()
    undescribed = run(world.coordinator.run(
        request(REQ), ActionIntent(name=UNDESCRIBED.name), ActionScope(company_id=COMPANY),
        {"payout": "p", "amount": 1}))  # fmt: skip
    assert (undescribed.status, undescribed.reason) == (S.FAILED, R.APPROVAL_UNAVAILABLE)
    assert undescribed.approval_id is None and world.undescribed.executed == 0
    world.repository.fail = True
    down = run(world.run_budget(REQ))
    assert (down.status, down.reason, down.approval_id) == (S.FAILED, R.APPROVAL_UNAVAILABLE,
                                                            None)  # fmt: skip
    assert world.repository.rows == {} and world.budget.effects == []
    assert E.APPROVAL_REFUSED in [e.event_type for e in world.audit.events]


def test_concurrent_consumption_has_exactly_one_winner() -> None:
    world = ApprovalWorld()
    first = approved(world)

    async def race():
        return await asyncio.gather(*(world.run_budget(REQ, approval_id=first.approval_id)
                                      for _ in range(10)))  # fmt: skip

    results = run(race())
    assert sorted(r.status.value for r in results) == ["failed"] * 9 + ["verified"]
    assert {r.reason for r in results if r.status is S.FAILED} == {R.APPROVAL_ALREADY_CONSUMED}
    assert len(world.budget.effects) == 1


def test_raw_parameters_never_reach_the_approval_store() -> None:
    world = ApprovalWorld()
    first = run(world.coordinator.run(
        request(REQ), ActionIntent(name="test.budget.update"),
        ActionScope(company_id=COMPANY, store_id=STORE_A),
        {"campaign": "spring", "amount": 150, "reason": "RAW-REASON-MARKER-77"}))  # fmt: skip
    stored = world.repository.rows[first.approval_id]
    dumped = stored.model_dump_json()
    # The reason is shown to the approver only through the trusted summary text the
    # handler chose to write; the raw parameter mapping itself is never stored.
    assert '"amount":' not in dumped and '"campaign":' not in dumped and '"reason":' not in dumped
    assert stored.summary.description == "Campaign spring: RAW-REASON-MARKER-77"
    assert len(stored.subject_fingerprint) == 64
    assert set(type(stored).model_fields) >= {"subject_fingerprint", "summary"}
    assert not {"parameters", "raw", "payload", "input"} & set(type(stored).model_fields)


@pytest.mark.parametrize("requested_as, presented_as", [
    ("user", "api_client"), ("user", "system_agent"), ("api_client", "user"),
])  # fmt: skip
def test_same_actor_id_under_another_actor_type_never_executes(requested_as, presented_as):
    world = ApprovalWorld()
    owner = actor("requester-1", REQUESTER, stores=REQ.store_ids, actor_type=requested_as)
    first = requested(world, who=owner)
    run(world.service.approve(request(APP), first.approval_id, None))
    twin = actor("requester-1", REQUESTER, stores=REQ.store_ids, actor_type=presented_as)
    refused = run(world.run_budget(twin, approval_id=first.approval_id))
    assert (refused.status, refused.reason) == (S.FAILED, R.APPROVAL_MISMATCH)
    assert world.budget.effects == []
    assert world.repository.rows[first.approval_id].consumed_at is None  # still usable
    done = run(world.run_budget(owner, approval_id=first.approval_id))
    assert done.status is S.VERIFIED and len(world.budget.effects) == 1
    (claimed,) = [e for e in world.repository.history
                  if e.event_type is ApprovalEventType.EXECUTION_CLAIMED]  # fmt: skip
    assert (claimed.actor_id, claimed.actor_type) == ("requester-1", requested_as)
