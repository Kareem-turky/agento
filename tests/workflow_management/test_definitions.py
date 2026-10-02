"""Workflow definitions, the static Product catalog, the trusted runtime registry, the
explicit state machines and the durable records (Task 034)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.workflow_management import (
    DAILY_REPORT_WORKFLOW,
    CheckpointPolicy,
    ProductWorkflowCatalog,
    StepSideEffect,
    WorkflowDefinition,
    WorkflowStepDefinition,
    build_default_workflow_catalog,
)
from app.workflow_management.handlers import (
    ActionRunCheckpoint,
    GovernedWriteStepHandler,
    ReadOnlyStepHandler,
    StepResult,
    WorkflowRegistrationError,
    WorkflowRuntimeRegistration,
    WorkflowRuntimeRegistry,
)
from app.workflow_management.records import StepAttemptRecord, WorkflowRunRecord
from app.workflow_management.state import (
    InvalidTransitionError,
    StepAttemptStatus,
    WorkflowRunStatus,
    check_run_transition,
    check_step_transition,
)
from tests.execution.fakes import coordinator as build_coordinator
from tests.support.workflow_fakes import (
    TEST_CATALOG,
    THREE_STEPS,
    EmptyInput,
    TestInput,
    registry,
    three_step_handlers,
    write_handlers,
)

R, S = WorkflowRunStatus, StepAttemptStatus
NOW = datetime(2026, 3, 3, tzinfo=UTC)


def step(**overrides) -> WorkflowStepDefinition:
    data = {
        "step_id": "one",
        "name": "One",
        "description": "A step.",
        "handler_id": "x.one",
        "side_effect": StepSideEffect.READ_ONLY,
        "timeout_seconds": 5,
        "max_attempts": 1,
    }
    return WorkflowStepDefinition(**(data | overrides))  # fmt: skip


def workflow(**overrides) -> WorkflowDefinition:
    data = {
        "workflow_id": "x.flow",
        "name": "Flow",
        "description": "A flow.",
        "category": "operations",
        "version": 1,
        "steps": (step(),),
    }
    return WorkflowDefinition(**(data | overrides))  # fmt: skip


# ----- definitions ------------------------------------------------------------------------------


def test_definitions_are_immutable_metadata_without_code_references() -> None:
    definition = workflow()
    with pytest.raises(ValidationError):
        definition.name = "changed"  # type: ignore[misc]
    for field in ("module", "callable", "class_path", "import_path", "provider", "credential"):
        with pytest.raises(ValidationError):
            workflow(**{field: "app.workflows:Thing"})
        with pytest.raises(ValidationError):
            step(**{field: "app.workflows:Thing"})
    for bad in ("app.workflows:Thing", "x", "X.Y", "a..b", "a.b c", ""):
        with pytest.raises(ValidationError):
            step(handler_id=bad)
        with pytest.raises(ValidationError):
            workflow(workflow_id=bad)


def test_steps_are_bounded_ordered_and_unique() -> None:
    with pytest.raises(ValidationError):
        workflow(steps=())
    with pytest.raises(ValidationError):
        workflow(steps=(step(), step()))
    for bad in ({"timeout_seconds": 0}, {"timeout_seconds": 301}, {"max_attempts": 0},
                {"max_attempts": 6}):  # fmt: skip
        with pytest.raises(ValidationError):
            step(**bad)
    flow = workflow(steps=(step(step_id="b"), step(step_id="a")))
    assert [s.step_id for s in flow.steps] == ["b", "a"]  # declared order is execution order
    assert flow.max_duration_seconds == 10


def test_a_governed_write_step_runs_at_most_once() -> None:
    with pytest.raises(ValidationError):
        step(side_effect=StepSideEffect.GOVERNED_WRITE, max_attempts=2)
    assert step(side_effect=StepSideEffect.GOVERNED_WRITE).max_attempts == 1
    assert [e.value for e in StepSideEffect] == ["read_only", "governed_write"]


# ----- the production catalog ---------------------------------------------------------------


def test_the_production_catalog_holds_exactly_the_daily_report_workflow() -> None:
    catalog = build_default_workflow_catalog()
    assert catalog.workflow_ids == {"operations.daily_report"}
    (daily,) = catalog.definitions()
    assert daily is DAILY_REPORT_WORKFLOW
    assert daily.version == 1 and daily.category.value == "operations"
    (only,) = daily.steps
    assert (only.step_id, only.handler_id, only.side_effect, only.checkpoint_policy) == (
        "compute_daily_report", "operations.daily_report.compute", StepSideEffect.READ_ONLY,
        CheckpointPolicy.NONE,
    )  # fmt: skip
    assert (only.timeout_seconds, only.max_attempts) == (30, 1)
    assert [(f.name, f.kind.value, f.required) for f in daily.inputs] == [
        ("business_date", "date", False)]  # fmt: skip
    # Test-only Workflows never enter the production catalog.
    assert not {"testing.three_steps", "testing.governed_write"} & catalog.workflow_ids


def test_the_catalog_is_immutable_and_rejects_duplicates_and_foreign_values() -> None:
    catalog = build_default_workflow_catalog()
    with pytest.raises(AttributeError):
        catalog._workflows = {}  # type: ignore[misc]
    with pytest.raises(ValueError):
        ProductWorkflowCatalog([workflow(), workflow()])
    with pytest.raises(TypeError):
        ProductWorkflowCatalog([{"workflow_id": "x.flow"}])  # type: ignore[list-item]
    assert catalog.get("operations.daily_report") is DAILY_REPORT_WORKFLOW
    assert catalog.get("unknown.flow") is None and len(catalog) == 1


# ----- the runtime registry -----------------------------------------------------------------


def test_registry_binds_exactly_the_catalog_steps() -> None:
    bindings = registry(three=three_step_handlers())
    assert bindings.workflow_ids == {"testing.three_steps"}
    assert bindings.input_model("testing.three_steps") is TestInput
    assert bindings.handler("testing.three_steps", "testing.fetch") is not None
    assert bindings.handler("testing.three_steps", "unknown.handler") is None
    with pytest.raises(AttributeError):
        bindings._bindings = {}  # type: ignore[misc]


def test_registry_fails_closed_on_any_inconsistent_binding() -> None:
    handlers = three_step_handlers()
    cases = [
        [WorkflowRuntimeRegistration("unknown.flow", TestInput, tuple(handlers.values()))],
        [WorkflowRuntimeRegistration("testing.three_steps", TestInput,
                                     (handlers["fetch"], handlers["transform"]))],  # missing
        [WorkflowRuntimeRegistration("testing.three_steps", TestInput,
                                     (*handlers.values(), handlers["fetch"]))],  # duplicate
        [WorkflowRuntimeRegistration("testing.three_steps", dict,  # type: ignore[arg-type]
                                     tuple(handlers.values()))],
        [WorkflowRuntimeRegistration("testing.three_steps", TestInput,
                                     (*handlers.values(), object()))],  # type: ignore[arg-type]
        [WorkflowRuntimeRegistration("testing.three_steps", TestInput, tuple(handlers.values()))]
        * 2,
    ]  # fmt: skip
    for registrations in cases:
        with pytest.raises(WorkflowRegistrationError):
            WorkflowRuntimeRegistry(TEST_CATALOG, registrations)


def test_registry_rejects_a_wrong_checkpoint_or_side_effect_binding() -> None:
    handlers = three_step_handlers()
    handlers["finish"].checkpoint_model = TestInput  # finish declares no checkpoint
    with pytest.raises(WorkflowRegistrationError):
        registry(three=handlers)
    coordinator, _ = build_coordinator()
    write = write_handlers(coordinator)
    # A read-only handler bound to the governed write Step.
    write["write"] = three_step_handlers()["finish"]
    write["write"].handler_id = "testing.write_note"
    with pytest.raises(WorkflowRegistrationError):
        registry(write=write)


def test_a_governed_write_handler_cannot_bypass_the_coordinator() -> None:
    coordinator, _ = build_coordinator()

    class Bypass(GovernedWriteStepHandler):
        handler_id = "testing.write_note"

        def action(self, context, run_input, checkpoints):  # pragma: no cover - never used
            raise AssertionError

        async def execute(self, context, run_input, checkpoints):  # the bypass attempt
            return StepResult("completed")  # type: ignore[arg-type]

    write = write_handlers(coordinator)
    write["write"] = Bypass(coordinator)
    with pytest.raises(WorkflowRegistrationError):
        registry(write=write)
    with pytest.raises(TypeError):
        Bypass(object())  # type: ignore[arg-type]
    assert GovernedWriteStepHandler.checkpoint_model is ActionRunCheckpoint
    assert ReadOnlyStepHandler.side_effect is StepSideEffect.READ_ONLY


def test_an_input_model_must_be_frozen_and_strict() -> None:
    class Loose(BaseModel):
        value: int

    class Frozen(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")

    handlers = three_step_handlers()
    with pytest.raises(WorkflowRegistrationError):
        WorkflowRuntimeRegistry(TEST_CATALOG, [WorkflowRuntimeRegistration(
            "testing.three_steps", Loose, tuple(handlers.values()))])  # fmt: skip
    WorkflowRuntimeRegistry(TEST_CATALOG, [WorkflowRuntimeRegistration(
        "testing.three_steps", Frozen, tuple(handlers.values()))])  # fmt: skip
    assert EmptyInput.model_config["frozen"] is True
    assert THREE_STEPS.workflow_id == "testing.three_steps"


# ----- state machines -------------------------------------------------------------------------


def test_run_state_machine_is_explicit() -> None:
    allowed = {(R.PENDING, R.RUNNING), (R.PENDING, R.FAILED), (R.RUNNING, R.RUNNING),
               *((R.RUNNING, t) for t in (R.SUCCEEDED, R.FAILED, R.REQUIRES_HUMAN,
                                          R.AWAITING_APPROVAL))}  # fmt: skip
    for current in R:
        for new in R:
            if (current, new) in allowed:
                check_run_transition(current, new)
            else:
                with pytest.raises(InvalidTransitionError):
                    check_run_transition(current, new)


def test_step_state_machine_is_explicit() -> None:
    for current in S:
        for new in S:
            if current is S.RUNNING and new is not S.RUNNING:
                check_step_transition(current, new)
            else:
                with pytest.raises(InvalidTransitionError):
                    check_step_transition(current, new)


# ----- records ------------------------------------------------------------------------------


def run_record(**overrides) -> WorkflowRunRecord:
    data = {"run_id": uuid4(), "workflow_id": "x.flow", "workflow_version": 1,
            "request_id": uuid4(), "company_id": "c", "actor_id": "a", "actor_type": "user",
            "channel": "api", "store_id": None, "status": "pending", "current_step_id": None,
            "failure_code": None, "input_state": {}, "input_fingerprint": "0" * 64,
            "lease_owner": None, "lease_expires_at": None, "created_at": NOW,
            "updated_at": NOW, "completed_at": None}  # fmt: skip
    return WorkflowRunRecord.model_validate(data | overrides)


def test_run_records_reject_inconsistent_or_unsafe_state() -> None:
    run_record()
    for bad in (
        {"status": "exploded"},
        {"status": "succeeded"},  # terminal without completed_at
        {"completed_at": NOW},  # active with completed_at
        {"status": "failed", "completed_at": NOW},  # failed without a code
        {"status": "succeeded", "completed_at": NOW, "failure_code": "step_timeout"},
        {"lease_owner": uuid4()},  # half a claim
        {"status": "failed", "completed_at": NOW, "failure_code": "access_denied",
         "lease_owner": uuid4(), "lease_expires_at": NOW},  # terminal with a claim
        {"input_state": {"x": "y" * 5000}},  # oversized
        {"input_state": []},
        {"failure_code": "Traceback (most recent call last)"},
        {"workflow_id": "app.module:Class"},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            run_record(**bad)


def test_attempt_records_keep_checkpoints_bounded_and_succeeded_only() -> None:
    data = {"run_id": uuid4(), "company_id": "c", "step_id": "one", "attempt": 1,
            "handler_id": "x.one", "status": "succeeded", "failure_code": None,
            "verification_code": "verified", "checkpoint": {"k": 1}, "started_at": NOW,
            "completed_at": NOW}  # fmt: skip
    StepAttemptRecord.model_validate(data)
    for bad in (
        {"checkpoint": {"k": "v" * 9000}},
        {"checkpoint": [1, 2]},
        {"status": "failed", "failure_code": "step_timeout"},  # failed with a checkpoint
        {"verification_code": None},  # succeeded without verification
        {"status": "running"},  # running with completed_at
        {"attempt": 0},
        {"attempt": 21},
    ):
        with pytest.raises(ValidationError):
            StepAttemptRecord.model_validate(data | bad)
