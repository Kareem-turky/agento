"""Workflow observability (Task 034): one low-cardinality observation per run and per Step
attempt (workflow id, step id, status, safe failure code, retry flag), through the existing
Product observability. Never a run, company, actor or store id in metrics, never input,
checkpoint or output, and an observability failure never changes the run."""

import asyncio

import pytest

from app.observability import ObservationOutcome, ProductOperation, WorkflowDetails
from app.workflow_management.state import StepAttemptStatus, WorkflowRunStatus
from tests.support.observability import (
    FAILURE_POINTS,
    FailingObservability,
    RecordingObservability,
    harness,
)
from tests.support.workflow_fakes import (
    InMemoryWorkflowRunRepository,
    TestInput,
    engine,
    registry,
    request,
    scope,
    three_step_handlers,
)


def execute(observability, **scripts):
    repository = InMemoryWorkflowRunRepository()
    platform = engine(repository, registry(three=three_step_handlers(**scripts)),
                      observability=observability)  # fmt: skip
    req = request()
    result = asyncio.run(platform.execute("testing.three_steps", req, scope(),
                                          TestInput(value=424242)))  # fmt: skip
    return result, req


def test_runs_and_attempts_are_observed_with_bounded_labels() -> None:
    recording = RecordingObservability()
    result, req = execute(recording, fetch=["error", "ok"], transform=["human"])
    (run,) = recording.of(ProductOperation.WORKFLOW_RUN)
    assert run.request_id == req.request_id and run.outcome is ObservationOutcome.ERROR
    assert run.attributes == {"workflow.id": "testing.three_steps",
                              "workflow.status": "requires_human",
                              "workflow.failure_code": "step_outcome_uncertain"}  # fmt: skip
    attempts = recording.of(ProductOperation.WORKFLOW_STEP_ATTEMPT)
    assert [(a.outcome, a.attributes) for a in attempts] == [
        (ObservationOutcome.ERROR, {"workflow.id": "testing.three_steps",
                                    "workflow.step_id": "fetch", "workflow.status": "failed",
                                    "workflow.failure_code": "step_execution_failed",
                                    "workflow.retry": True}),
        (ObservationOutcome.COMPLETED, {"workflow.id": "testing.three_steps",
                                        "workflow.step_id": "fetch",
                                        "workflow.status": "succeeded", "workflow.retry": False}),
        (ObservationOutcome.ERROR, {"workflow.id": "testing.three_steps",
                                    "workflow.step_id": "transform",
                                    "workflow.status": "requires_human",
                                    "workflow.failure_code": "step_outcome_uncertain",
                                    "workflow.retry": False}),
    ]  # fmt: skip
    assert result.status is WorkflowRunStatus.REQUIRES_HUMAN


def test_metrics_spans_and_logs_never_carry_identifiers_or_content() -> None:
    h = harness()
    result, req = execute(h.observability, transform=["timeout", "ok"])
    assert result.status is WorkflowRunStatus.SUCCEEDED
    points = h.metric_points()
    counted = [a for a, _ in points["product.operation.count"]
               if a["operation"].startswith("workflow.")]  # fmt: skip
    assert {a["operation"] for a in counted} == {"workflow.run", "workflow.step_attempt"}
    assert any(a.get("workflow.status") == "timed_out" and a.get("workflow.retry") is True
               for a in counted)  # fmt: skip
    for attributes in counted + [a for a, _ in points["product.operation.duration"]]:
        assert set(attributes) <= {"operation", "outcome", "workflow.id", "workflow.step_id",
                                   "workflow.status", "workflow.failure_code",
                                   "workflow.retry"}, attributes  # fmt: skip
    rendered = (
        repr(points) + repr([dict(s.attributes or {}) for s in h.finished_spans()]) + repr(h.logs())
    )
    for leak in (str(result.run_id), "company-1", "store-a", "user-1", "424242", "848484"):
        assert leak not in rendered, leak


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_an_observability_failure_never_changes_the_run(where: str) -> None:
    result, _ = execute(FailingObservability(where))
    assert result.status is WorkflowRunStatus.SUCCEEDED
    assert result.output("finish") == {"result": 848484}


def test_workflow_details_are_bounded_by_construction() -> None:
    WorkflowDetails(workflow_id="testing.three_steps", status=StepAttemptStatus.FAILED,
                    step_id="fetch", retry=True)  # fmt: skip
    for bad in (
        {"workflow_id": "f9a1c1e0-0000-4000-8000-000000000000"},  # a run id is not a label
        {"workflow_id": "company-1"},
        {"step_id": "Bad Step"},
        {"status": "failed"},  # not a canonical enum member
        {"retry": "yes"},
    ):
        data = {"workflow_id": "testing.three_steps", "status": StepAttemptStatus.FAILED} | bad
        with pytest.raises(ValueError):
            WorkflowDetails(**data)  # type: ignore[arg-type]
