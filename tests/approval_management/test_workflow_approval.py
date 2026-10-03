"""Workflow continuation after a human approval (Task 036), on a TEST-ONLY Workflow:

    fetch (read) -> budget (governed MEDIUM_RISK write, max_attempts=1) -> report (read)

The write Step stops the run ``awaiting_approval`` (no effect, the next Step never runs);
after another human approves, the requester continues it through the explicit approval
continuation: the same stored input yields the same action input, the approval is
consumed once, the write executes exactly once and the run succeeds. Rejected / expired
approvals never resume; the approval wait is not a retry. In-memory repositories."""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue, StrictInt, StrictStr

from app.approval_management.errors import (
    ApprovalAccessDeniedError,
    ApprovalConflictError,
    ApprovalNotResumableError,
)
from app.approval_management.service import ApprovalService
from app.governance import ActionIntent, ActionScope
from app.workflow_management.catalog import ProductWorkflowCatalog
from app.workflow_management.definitions import (
    CheckpointPolicy,
    StepSideEffect,
    WorkflowCategory,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from app.workflow_management.engine import WorkflowEngine
from app.workflow_management.errors import WorkflowAccessDeniedError, WorkflowNotResumableError
from app.workflow_management.handlers import (
    Checkpoints,
    GovernedWriteStepHandler,
    ReadOnlyStepHandler,
    StepOutcome,
    StepResult,
    WorkflowRuntimeRegistration,
    WorkflowRuntimeRegistry,
    WorkflowStepContext,
)
from app.workflow_management.state import WorkflowRunStatus as R
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
from tests.support.workflow_fakes import InMemoryWorkflowRunRepository, StepClock

_FROZEN = ConfigDict(frozen=True, extra="forbid")
REQ = actor("requester-1", REQUESTER)
APP = actor("approver-1", APPROVER)


def run(coroutine):
    return asyncio.run(coroutine)


class BudgetRunInput(BaseModel):
    model_config = _FROZEN
    campaign: StrictStr
    amount: StrictInt


class Fetched(BaseModel):
    model_config = _FROZEN
    campaign: StrictStr


def _step(step_id: str, handler_id: str, side_effect: StepSideEffect,
          checkpoint: bool) -> WorkflowStepDefinition:  # fmt: skip
    return WorkflowStepDefinition(
        step_id=step_id, name=step_id.title(), description=f"Test step {step_id}.",
        handler_id=handler_id, side_effect=side_effect, timeout_seconds=2, max_attempts=1,
        checkpoint_policy=CheckpointPolicy.STATE if checkpoint else CheckpointPolicy.NONE,
    )  # fmt: skip


APPROVAL_WORKFLOW = WorkflowDefinition(
    workflow_id="testing.approval_budget", name="Approval budget", description="TEST ONLY.",
    category=WorkflowCategory.OPERATIONS, version=1,
    steps=(
        _step("fetch", "testing.fetch_campaign", StepSideEffect.READ_ONLY, True),
        _step("budget", "testing.budget_write", StepSideEffect.GOVERNED_WRITE, True),
        _step("report", "testing.report", StepSideEffect.READ_ONLY, False),
    ),
)  # fmt: skip
CATALOG = ProductWorkflowCatalog([APPROVAL_WORKFLOW])


class FetchCampaign(ReadOnlyStepHandler):
    handler_id = "testing.fetch_campaign"
    checkpoint_model = Fetched

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: WorkflowStepContext, run_input: BaseModel,
                      checkpoints: Checkpoints) -> StepResult:  # fmt: skip
        self.calls += 1
        assert isinstance(run_input, BudgetRunInput)
        return StepResult(StepOutcome.COMPLETED, checkpoint=Fetched(campaign=run_input.campaign))

    async def verify(self, context: WorkflowStepContext, run_input: BaseModel,
                     checkpoints: Checkpoints, result: StepResult) -> bool:  # fmt: skip
        return True


class BudgetWrite(GovernedWriteStepHandler):
    handler_id = "testing.budget_write"

    def action(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> tuple[ActionIntent, Mapping[str, JsonValue]]:
        fetched = checkpoints["fetch"]
        assert isinstance(fetched, Fetched) and isinstance(run_input, BudgetRunInput)
        return ActionIntent(name=BUDGET_UPDATE.name), {
            "campaign": fetched.campaign,
            "amount": run_input.amount,
            "reason": "Workflow",
        }


class Report(ReadOnlyStepHandler):
    handler_id = "testing.report"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: WorkflowStepContext, run_input: BaseModel,
                      checkpoints: Checkpoints) -> StepResult:  # fmt: skip
        self.calls += 1
        return StepResult(StepOutcome.COMPLETED, output="reported")

    async def verify(self, context: WorkflowStepContext, run_input: BaseModel,
                     checkpoints: Checkpoints, result: StepResult) -> bool:  # fmt: skip
        return True


class Env:
    def __init__(self) -> None:
        self.world = ApprovalWorld()
        self.repository = InMemoryWorkflowRunRepository()
        self.fetch, self.report = FetchCampaign(), Report()
        self.bindings = WorkflowRuntimeRegistry(CATALOG, [WorkflowRuntimeRegistration(
            APPROVAL_WORKFLOW.workflow_id, BudgetRunInput,
            (self.fetch, BudgetWrite(self.world.coordinator), self.report))])  # fmt: skip
        self.engine = self.new_engine()
        w = self.world
        self.service = ApprovalService(w.repository, w.service._gate, w.service._coordinator,
                                       clock=w.clock, workflows=self.engine)  # fmt: skip

    def new_engine(self) -> WorkflowEngine:
        return WorkflowEngine(CATALOG, self.bindings, self.repository, clock=StepClock())

    def start(self) -> Any:
        return run(self.engine.execute(APPROVAL_WORKFLOW.workflow_id, request(REQ),
                                       ActionScope(company_id=COMPANY, store_id=STORE_A),
                                       {"campaign": "spring", "amount": 150}))  # fmt: skip

    def attempts(self, run_id) -> list[dict]:
        return [a for a in self.repository.attempt_rows(run_id) if a["step_id"] == "budget"]


def awaiting(env: Env):
    result = env.start()
    assert (result.status, result.failure_code.value) == (R.AWAITING_APPROVAL,
                                                          "approval_required")  # fmt: skip
    (attempt,) = env.attempts(result.run_id)
    assert attempt["status"] == "awaiting_approval" and attempt["approval_id"]
    return result, env.world.repository.rows[_uuid(attempt["approval_id"])]


def _uuid(value):
    from uuid import UUID

    return UUID(str(value))


def test_approved_workflow_resumes_and_writes_exactly_once() -> None:
    env = Env()
    result, approval = awaiting(env)
    assert env.world.budget.effects == [] and env.report.calls == 0 and env.fetch.calls == 1
    assert approval.source.kind.value == "workflow_step"
    assert (approval.source.workflow_run_id, approval.source.workflow_id,
            approval.source.workflow_step_id) == (result.run_id, "testing.approval_budget",
                                                  "budget")  # fmt: skip
    # The write Step allows ONE attempt: the approval wait does not consume a retry.
    assert APPROVAL_WORKFLOW.steps[1].max_attempts == 1
    run(env.world.service.approve(request(APP), approval.approval_id, None))
    resumed = run(env.service.resume_workflow(request(REQ), approval.approval_id))
    assert (resumed.status, resumed.failure_code) == ("succeeded", None)
    assert env.world.budget.effects == [(STORE_A, "spring", 150)]
    assert env.fetch.calls == 1 and env.report.calls == 1  # fetch was never re-run
    attempts = env.attempts(result.run_id)
    assert [(a["attempt"], a["status"]) for a in attempts] == [(1, "awaiting_approval"),
                                                               (2, "succeeded")]  # fmt: skip
    assert {a["approval_id"] for a in attempts} == {str(approval.approval_id)}
    events = env.repository.event_types(result.run_id)
    assert "workflow_approval_resumed" in events and events[-1] == "workflow_succeeded"
    assert "step_retrying" not in events
    stored = env.world.repository.rows[approval.approval_id]
    assert stored.consumed and stored.execution_outcome.value == "verified"


def test_resume_survives_an_engine_restart() -> None:
    env = Env()
    result, approval = awaiting(env)
    run(env.world.service.approve(request(APP), approval.approval_id, None))
    restarted = env.new_engine()  # a new process: only durable state survives
    final = run(restarted.resume_after_approval(request(REQ), result.run_id,
                                                approval.approval_id))  # fmt: skip
    assert final.status is R.SUCCEEDED and env.fetch.calls == 1
    assert len(env.world.budget.effects) == 1


def test_rejected_or_expired_approval_never_resumes() -> None:
    env = Env()
    result, approval = awaiting(env)
    run(env.world.service.reject(request(APP), approval.approval_id, "no"))
    with pytest.raises(ApprovalNotResumableError):
        run(env.service.resume_workflow(request(REQ), approval.approval_id))
    assert env.repository.runs[result.run_id]["status"] == "awaiting_approval"
    env2 = Env()
    result2, approval2 = awaiting(env2)
    env2.world.clock.advance(hours=25)
    with pytest.raises(ApprovalNotResumableError):
        run(env2.service.resume_workflow(request(REQ), approval2.approval_id))
    assert env2.world.repository.rows[approval2.approval_id].status.value == "expired"
    for e in (env, env2):
        assert e.world.budget.effects == [] and e.report.calls == 0


def test_only_the_requester_continues_and_generic_resume_stays_closed() -> None:
    env = Env()
    result, approval = awaiting(env)
    with pytest.raises(WorkflowNotResumableError):
        run(env.engine.resume(request(REQ), result.run_id))  # never a generic reopen
    run(env.world.service.approve(request(APP), approval.approval_id, None))
    with pytest.raises(ApprovalAccessDeniedError):
        run(env.service.resume_workflow(request(APP), approval.approval_id))
    assert env.world.budget.effects == []


def test_concurrent_resume_writes_once() -> None:
    env = Env()
    result, approval = awaiting(env)
    run(env.world.service.approve(request(APP), approval.approval_id, None))

    async def race():
        return await asyncio.gather(
            *(env.service.resume_workflow(request(REQ), approval.approval_id) for _ in range(5)),
            return_exceptions=True,
        )

    outcomes = run(race())
    succeeded = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(succeeded) == 1 and succeeded[0].status == "succeeded"
    assert all(isinstance(o, (ApprovalConflictError, ApprovalNotResumableError))
               for o in outcomes if isinstance(o, Exception))  # fmt: skip
    assert len(env.world.budget.effects) == 1 and env.report.calls == 1


def test_same_actor_id_under_another_actor_type_never_resumes_or_executes() -> None:
    env = Env()
    result, approval = awaiting(env)
    run(env.world.service.approve(request(APP), approval.approval_id, None))
    for kind in ("api_client", "system_agent"):
        twin = actor("requester-1", REQUESTER | APPROVER, actor_type=kind)
        with pytest.raises(ApprovalAccessDeniedError):
            run(env.service.resume_workflow(request(twin), approval.approval_id))
        with pytest.raises(WorkflowAccessDeniedError):
            run(env.engine.resume_after_approval(request(twin), result.run_id,
                                                 approval.approval_id))  # fmt: skip
    assert env.world.budget.effects == [] and env.report.calls == 0
    assert env.repository.runs[result.run_id]["status"] == "awaiting_approval"
    assert env.world.repository.rows[approval.approval_id].consumed_at is None
    # The real principal still continues it, exactly once.
    resumed = run(env.service.resume_workflow(request(REQ), approval.approval_id))
    assert resumed.status == "succeeded" and len(env.world.budget.effects) == 1
