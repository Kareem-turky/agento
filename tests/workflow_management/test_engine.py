"""The generic Workflow engine on TEST-ONLY Workflows (in-memory durable state).

Proves: ordered Steps with checkpoint passing, bounded read-only retries, real timeouts,
mandatory verification, safe checkpoints, terminal failure, governed writes only through
the real ExecutionCoordinator (never blindly retried; approval and uncertainty stop the
run), crash/restart recovery without re-running completed Steps, execution claims against
concurrent and stale executors, and fail-closed behaviour whenever durable state is lost.
"""

import asyncio
import concurrent.futures
import json
import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.context.models import RequestContext
from app.workflow_management.contracts import Claim, RunChange, WorkflowClaimConflictError
from app.workflow_management.errors import (
    WorkflowAccessDeniedError,
    WorkflowInputInvalidError,
    WorkflowLeaseConflictError,
    WorkflowNotResumableError,
    WorkflowRunNotFoundError,
    WorkflowUnavailableError,
)
from app.workflow_management.state import WorkflowRunStatus
from tests.execution.fakes import FakeHandler, RecordingAuditSink
from tests.execution.fakes import coordinator as build_coordinator
from tests.support.workflow_fakes import (
    OTHER_COMPANY,
    InMemoryWorkflowRunRepository,
    StepClock,
    TestInput,
    Transformed,
    engine,
    registry,
    request,
    scope,
    three_step_handlers,
    write_handlers,
)

R = WorkflowRunStatus


def run[T](coro) -> T:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def three(repository=None, clock=None, **scripts):
    repository = repository if repository is not None else InMemoryWorkflowRunRepository()
    handlers = three_step_handlers(**scripts)
    return engine(repository, registry(three=handlers), clock), repository, handlers


def execute(platform, value: int = 21, **kwargs):
    return run(platform.execute("testing.three_steps", kwargs.pop("req", request()),
                                kwargs.pop("scp", scope()), TestInput(value=value)))  # fmt: skip


def stored_text(repository) -> str:
    return json.dumps([list(repository.runs.values()), list(repository.attempts.values()),
                       repository.events], default=str)  # fmt: skip


def attempts(repository, run_id) -> list[tuple[str, int, str, str | None]]:
    return [(a["step_id"], a["attempt"], a["status"], a["failure_code"])
            for a in repository.attempt_rows(run_id)]  # fmt: skip


# ----- the happy path -----------------------------------------------------------------------


def test_ordered_steps_pass_typed_checkpoints_and_return_ephemeral_outputs() -> None:
    platform, repository, handlers = three()
    result = execute(platform)
    assert (result.status, result.failure_code) == (R.SUCCEEDED, None)
    assert result.output("finish") == {"result": 42}
    assert result.output("fetch") == {"fetched": 21}
    # Strictly in order; each Step saw exactly the checkpoints of the earlier Steps.
    assert [c for c in handlers["fetch"].calls] == [(1, {})]
    ((_, seen),) = handlers["transform"].calls
    assert set(seen) == {"fetch"} and seen["fetch"].value == 21
    ((_, seen),) = handlers["finish"].calls
    assert set(seen) == {"fetch", "transform"} and seen["transform"].doubled == 42
    # Durable control state: one verified attempt per Step, checkpoints only where declared,
    # and NEVER a Step output.
    rows = repository.attempt_rows(result.run_id)
    assert [(a["step_id"], a["status"], a["verification_code"]) for a in rows] == [
        ("fetch", "succeeded", "verified"), ("transform", "succeeded", "verified"),
        ("finish", "succeeded", "verified"),
    ]  # fmt: skip
    assert [a["checkpoint"] for a in rows] == [{"value": 21}, {"doubled": 42}, None]
    stored = stored_text(repository)
    assert '"result"' not in stored and "fetched" not in stored
    assert repository.event_types(result.run_id) == [
        "workflow_requested", "workflow_started",
        "step_started", "step_succeeded", "step_started", "step_succeeded",
        "step_started", "step_succeeded", "workflow_succeeded",
    ]  # fmt: skip
    row = repository.runs[result.run_id]
    assert row["status"] == "succeeded" and row["lease_owner"] is None
    assert row["completed_at"] is not None and row["input_state"] == {"value": 21}


def test_the_step_context_is_trusted_and_carries_no_input() -> None:
    platform, _, handlers = three()
    req = request()
    result = execute(platform, req=req)
    context = handlers["fetch"].contexts[0]
    assert context.workflow_run_id == result.run_id and context.attempt == 1
    assert context.request is req and context.actor == req.actor
    assert (context.company_id, context.store_id) == ("company-1", "store-a")
    assert context.workflow_version == 1 and context.step_id == "fetch"


# ----- validation before execution ----------------------------------------------------------


def test_invalid_input_unknown_workflow_and_untrusted_scope_create_no_run() -> None:
    platform, repository, handlers = three()
    with pytest.raises(WorkflowInputInvalidError):
        run(platform.execute("testing.three_steps", request(), scope(), {"value": "x"}))
    with pytest.raises(WorkflowInputInvalidError):
        run(platform.execute("testing.three_steps", request(), scope(), {"value": 1, "x": 2}))
    with pytest.raises(WorkflowUnavailableError):  # in the catalog, not registered here
        run(platform.execute("testing.governed_write", request(), scope(), {}))
    with pytest.raises(WorkflowUnavailableError):
        run(platform.execute("operations.daily_report", request(), scope(), {}))
    with pytest.raises(WorkflowAccessDeniedError):  # no trusted actor
        run(platform.execute("testing.three_steps", RequestContext(), scope(), {"value": 1}))
    with pytest.raises(WorkflowAccessDeniedError):  # scope outside the actor's company
        run(platform.execute("testing.three_steps", request(), scope(OTHER_COMPANY),
                             {"value": 1}))  # fmt: skip
    assert repository.runs == {} and handlers["fetch"].calls == []


# ----- retries, timeouts, verification, failure ------------------------------------------------


def test_a_read_only_step_is_retried_within_its_bounded_attempts() -> None:
    platform, repository, handlers = three(fetch=["error", "retryable", "ok"])
    result = execute(platform)
    assert result.status is R.SUCCEEDED
    assert attempts(repository, result.run_id)[:3] == [
        ("fetch", 1, "failed", "step_execution_failed"),
        ("fetch", 2, "failed", "step_execution_failed"),
        ("fetch", 3, "succeeded", None),
    ]  # fmt: skip
    assert repository.event_types(result.run_id).count("step_retrying") == 2
    assert len(handlers["fetch"].calls) == 3  # never more than max_attempts


def test_retries_are_finite_and_exhaustion_fails_the_run() -> None:
    platform, repository, handlers = three(fetch=["error"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "retry_exhausted")
    assert len(handlers["fetch"].calls) == 3 and handlers["transform"].calls == []
    assert repository.event_types(result.run_id)[-2:] == ["step_failed", "workflow_failed"]
    # The raw exception text is recorded nowhere.
    assert "SENSITIVE" not in stored_text(repository)


def test_a_real_timeout_is_enforced_and_retried_for_a_read_only_step() -> None:
    platform, repository, handlers = three(fetch=["timeout", "ok"])
    result = execute(platform)
    assert result.status is R.SUCCEEDED
    assert attempts(repository, result.run_id)[:2] == [
        ("fetch", 1, "timed_out", "step_timeout"), ("fetch", 2, "succeeded", None),
    ]  # fmt: skip
    assert "step_timed_out" in repository.event_types(result.run_id)


def test_a_timeout_on_the_last_attempt_fails_safely() -> None:
    platform, repository, handlers = three(transform=["timeout"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "retry_exhausted")
    assert attempts(repository, result.run_id)[-1] == ("transform", 2, "timed_out", "step_timeout")
    assert handlers["finish"].calls == []


@pytest.mark.parametrize("behaviour", ["unverified", "verify_error"])
def test_a_step_is_never_succeeded_without_verification(behaviour: str) -> None:
    platform, repository, handlers = three(transform=[behaviour, "ok"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "step_verification_failed")
    (row,) = [a for a in repository.attempt_rows(result.run_id) if a["step_id"] == "transform"]
    assert (row["status"], row["verification_code"]) == ("failed", "not_verified")
    assert len(handlers["transform"].calls) == 1  # a verification failure is not retried
    assert handlers["finish"].calls == []


@pytest.mark.parametrize("behaviour", ["bad_checkpoint", "big_checkpoint"])
def test_an_invalid_or_oversized_checkpoint_is_never_stored(behaviour: str) -> None:
    platform, repository, handlers = three(fetch=[behaviour])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "checkpoint_invalid")
    assert all(a["checkpoint"] is None for a in repository.attempt_rows(result.run_id))


def test_a_confirmed_failure_and_a_denial_are_terminal_without_retry() -> None:
    platform, repository, handlers = three(fetch=["fail"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "step_execution_failed")
    assert len(handlers["fetch"].calls) == 1
    platform, repository, handlers = three(fetch=["deny"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "access_denied")
    assert len(handlers["fetch"].calls) == 1


def test_an_invalid_step_result_is_an_execution_failure() -> None:
    platform, repository, handlers = three(finish=["bad_result"])
    result = execute(platform)
    assert result.status is R.FAILED and result.failure_code.value == "step_execution_failed"


def test_requires_human_and_awaiting_approval_stop_every_later_step() -> None:
    platform, repository, handlers = three(transform=["human"])
    result = execute(platform)
    assert (result.status, result.failure_code.value) == (R.REQUIRES_HUMAN,
                                                          "step_outcome_uncertain")  # fmt: skip
    assert handlers["finish"].calls == [] and len(handlers["transform"].calls) == 1
    platform, repository, handlers = three(transform=["approval"])
    result = execute(platform)
    assert result.status is R.AWAITING_APPROVAL and handlers["finish"].calls == []
    assert repository.event_types(result.run_id)[-1] == "workflow_awaiting_approval"
    # v1 never auto-approves or fakes a continuation.
    with pytest.raises(WorkflowNotResumableError):
        run(platform.resume(request(), result.run_id))


# ----- governed writes ------------------------------------------------------------------------


def write_world(execute_behaviour: str = "ok", verify_behaviour: str = "ok",
                intent: str = "notes.add", **scripts):  # fmt: skip
    note = FakeHandler(execute_behaviour=execute_behaviour, verify_behaviour=verify_behaviour)
    sink = RecordingAuditSink()
    coordinator, _ = build_coordinator(note, sink=sink)
    handlers = write_handlers(coordinator, **scripts)
    handlers["write"].intent_name = intent
    repository = InMemoryWorkflowRunRepository()
    platform = engine(repository, registry(write=handlers))
    return platform, repository, handlers, note, sink


def run_write(platform):
    return run(platform.execute("testing.governed_write", request(), scope(), {}))


def test_a_verified_write_goes_through_the_coordinator_and_is_audited() -> None:
    platform, repository, handlers, note, sink = write_world()
    result = run_write(platform)
    assert result.status is R.SUCCEEDED
    assert len(note.execute_calls) == 1 and len(note.verify_calls) == 1
    assert "verified" in [t.value for t in sink.types]  # the action audit, separately
    write_row = [a for a in repository.attempt_rows(result.run_id) if a["step_id"] == "write"][0]
    assert write_row["checkpoint"]["action_run_id"] == str(result.output("write").run_id)
    assert set(write_row["checkpoint"]) == {"action_run_id", "reference_id"}
    assert len(handlers["after"].calls) == 1


@pytest.mark.parametrize(
    ("execute_behaviour", "verify_behaviour", "status", "code"),
    [
        ("ok", "mismatch", R.REQUIRES_HUMAN, "step_outcome_uncertain"),  # unverified ActionRun
        ("uncertain", "ok", R.REQUIRES_HUMAN, "step_outcome_uncertain"),
        ("crash", "ok", R.REQUIRES_HUMAN, "step_outcome_uncertain"),
        ("no_effect", "ok", R.FAILED, "step_execution_failed"),  # confirmed: nothing written
    ],
)  # fmt: skip
def test_an_unverified_write_is_never_workflow_success_and_never_retried(
    execute_behaviour, verify_behaviour, status, code
) -> None:
    platform, repository, handlers, note, sink = write_world(execute_behaviour, verify_behaviour)
    result = run_write(platform)
    assert (result.status, result.failure_code.value) == (status, code)
    assert len(note.execute_calls) == 1  # exactly once: never blindly retried
    assert handlers["after"].calls == []
    assert repository.runs[result.run_id]["lease_owner"] is None


def test_approval_required_stops_the_workflow_before_any_effect() -> None:
    """Without a human-approval broker (and a handler able to describe the decision) an
    approval-required write fails closed before any effect. The awaiting -> approve ->
    continue path is proven in tests/approval_management/test_workflow_approval.py."""
    platform, repository, handlers, note, sink = write_world(intent="orders.cancel")
    result = run_write(platform)
    assert (result.status, result.failure_code.value) == (R.FAILED, "step_execution_failed")
    assert note.execute_calls == [] and handlers["after"].calls == []
    write_row = [a for a in repository.attempt_rows(result.run_id) if a["step_id"] == "write"][0]
    assert write_row["status"] == "failed" and write_row["approval_id"] is None


def test_a_governance_denial_fails_the_write_step() -> None:
    platform, repository, handlers, note, sink = write_world(intent="labels.print")
    result = run_write(platform)
    # labels.print: the actor lacks the permission -> governance DENIES, nothing executes.
    assert (result.status, result.failure_code.value) == (R.FAILED, "access_denied")
    assert note.execute_calls == []


def test_a_write_timeout_requires_a_human_and_is_not_retried(monkeypatch) -> None:
    platform, repository, handlers, note, sink = write_world()

    async def slow(*args: Any, **kwargs: Any):
        await asyncio.sleep(30)

    monkeypatch.setattr(handlers["write"]._coordinator, "run", slow)
    result = run_write(platform)
    assert (result.status, result.failure_code.value) == (R.REQUIRES_HUMAN,
                                                          "step_outcome_uncertain")  # fmt: skip
    write_row = [a for a in repository.attempt_rows(result.run_id) if a["step_id"] == "write"][0]
    assert (write_row["status"], write_row["failure_code"]) == ("timed_out", "step_timeout")
    assert handlers["after"].calls == []


# ----- crash / restart recovery -----------------------------------------------------------------


class Crash(BaseException):
    """Simulated process loss (not an Exception: nothing in the engine may swallow it)."""


def test_resume_after_process_loss_never_reruns_a_completed_step() -> None:
    repository = InMemoryWorkflowRunRepository()
    clock = StepClock()
    first = three_step_handlers()

    def crash_in_transform(*_args: Any) -> Any:
        raise Crash()

    first["transform"].produce = crash_in_transform
    platform = engine(repository, registry(three=first), clock)
    with pytest.raises(Crash):
        execute(platform)
    (run_id,) = repository.runs
    # Step 1 succeeded and is durably checkpointed; step 2's attempt was left running.
    assert attempts(repository, run_id) == [("fetch", 1, "succeeded", None),
                                            ("transform", 1, "running", None)]  # fmt: skip
    # A live claim refuses a second executor.
    second = three_step_handlers()
    rebuilt = engine(repository, registry(three=second), clock)
    with pytest.raises(WorkflowLeaseConflictError):
        run(rebuilt.resume(request(), run_id))
    # The claim expires; a NEW engine (new process) resumes the SAME run.
    clock.advance(3600)
    result = run(rebuilt.resume(request(), run_id))
    assert (result.run_id, result.status) == (run_id, R.SUCCEEDED)
    assert second["fetch"].calls == []  # completed: NOT executed again
    ((_, seen),) = second["transform"].calls
    assert seen["fetch"].value == 21  # its checkpoint was reloaded from durable state
    assert result.output("finish") == {"result": 42}
    assert attempts(repository, run_id) == [
        ("fetch", 1, "succeeded", None), ("transform", 1, "failed", "executor_lost"),
        ("transform", 2, "succeeded", None), ("finish", 1, "succeeded", None),
    ]  # fmt: skip
    assert "workflow_resumed" in repository.event_types(run_id)


def test_a_lost_write_attempt_requires_a_human_on_resume() -> None:
    repository = InMemoryWorkflowRunRepository()
    clock = StepClock()
    note = FakeHandler()
    coordinator, _ = build_coordinator(note)
    handlers = write_handlers(coordinator)

    async def lost(*args: Any, **kwargs: Any):
        raise Crash()

    handlers["write"]._coordinator.run = lost  # the process dies mid-write
    platform = engine(repository, registry(write=handlers), clock)
    with pytest.raises(Crash):
        run(platform.execute("testing.governed_write", request(), scope(), {}))
    (run_id,) = repository.runs
    clock.advance(3600)
    fresh = write_handlers(build_coordinator(FakeHandler())[0])
    result = run(engine(repository, registry(write=fresh), clock).resume(request(), run_id))
    assert (result.status, result.failure_code.value) == (R.REQUIRES_HUMAN,
                                                          "step_outcome_uncertain")  # fmt: skip
    assert fresh["prepare"].calls == [] and fresh["after"].calls == []
    assert attempts(repository, run_id)[-1] == ("write", 1, "requires_human", "executor_lost")


def test_resume_is_company_scoped_and_refuses_terminal_runs() -> None:
    platform, repository, _ = three()
    result = execute(platform)
    with pytest.raises(WorkflowRunNotFoundError):  # another company: no existence oracle
        run(platform.resume(request(OTHER_COMPANY), result.run_id))
    with pytest.raises(WorkflowNotResumableError):
        run(platform.resume(request(), result.run_id))


# ----- execution claims ---------------------------------------------------------------------


def test_two_executors_never_progress_the_same_run() -> None:
    repository = InMemoryWorkflowRunRepository()
    gate = asyncio.Event()
    handlers = three_step_handlers()

    async def scenario():
        platform = engine(repository, registry(three=handlers))
        other = engine(repository, registry(three=three_step_handlers()))
        original = handlers["transform"].execute

        async def paused(*args):
            await gate.wait()
            return await original(*args)

        handlers["transform"].execute = paused
        first = asyncio.create_task(platform.execute("testing.three_steps", request(), scope(),
                                                     TestInput(value=1)))  # fmt: skip
        while not any(a["step_id"] == "transform" for a in repository.attempts.values()):
            await asyncio.sleep(0)
        (run_id,) = repository.runs
        with pytest.raises(WorkflowLeaseConflictError):
            await other.resume(request(), run_id)
        gate.set()
        return await first

    result = run(scenario())
    assert result.status is R.SUCCEEDED


def test_a_stale_executor_cannot_write_after_its_claim_was_taken_over() -> None:
    repository = InMemoryWorkflowRunRepository()
    clock = StepClock()
    handlers = three_step_handlers()
    platform = engine(repository, registry(three=handlers), clock)
    takeover: dict[str, Any] = {}

    def steal(i, c):
        # While the first executor is inside "transform", its claim expires and another
        # executor claims the run (a new token).
        (run_id,) = repository.runs
        clock.advance(3600)
        takeover["token"] = uuid.uuid4()
        run_in_thread(repository.claim("company-1", run_id, takeover["token"], clock(),
                                       clock.now))  # fmt: skip
        return Transformed(doubled=2), None

    handlers["transform"].produce = steal
    with pytest.raises(WorkflowLeaseConflictError):
        execute(platform, value=1)
    (run_id,) = repository.runs
    # The stale executor wrote nothing after the takeover: its transform attempt is still
    # running, and no later step started.
    assert attempts(repository, run_id)[-1] == ("transform", 1, "running", None)
    assert repository.runs[run_id]["lease_owner"] == str(takeover["token"])
    assert handlers["finish"].calls == []


def run_in_thread(coro):
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def test_a_stale_claim_token_is_refused_by_the_repository() -> None:
    platform, repository, _ = three(transform=["approval"])
    result = execute(platform)
    stale = Claim(run_id=result.run_id, company_id="company-1", token=uuid.uuid4())
    with pytest.raises(WorkflowClaimConflictError):
        run(repository.advance(stale, RunChange(
            expected_status=R.AWAITING_APPROVAL, status=R.RUNNING, current_step_id=None,
            lease_expires_at=None, updated_at=datetime.now(UTC))))  # fmt: skip


# ----- durable state is never optional --------------------------------------------------------


@pytest.mark.parametrize("operation", ["create_run", "claim", "advance", "list_attempts"])
def test_execution_stops_when_durable_state_is_unavailable(operation: str) -> None:
    repository = InMemoryWorkflowRunRepository()
    repository.fail_on = {operation}
    platform, _, handlers = three(repository)
    with pytest.raises(WorkflowUnavailableError):
        execute(platform)
    assert handlers["fetch"].calls == []  # never executed without durable state


@pytest.mark.parametrize(
    ("point", "ran_fetch"),
    [
        ("checkpoint", True),   # the Step's success (and checkpoint) could not be stored
        ("event", False),       # a required event could not be appended (step start)
        ("terminal", True),     # the terminal run state could not be stored
    ],
)  # fmt: skip
def test_lost_durability_mid_run_stops_the_run(point: str, ran_fetch: bool) -> None:
    repository = InMemoryWorkflowRunRepository()

    def fail(change: RunChange) -> bool:
        if point == "checkpoint":
            return change.finish_attempt is not None and change.finish_attempt.step_id == "fetch"
        if point == "event":
            return change.start_attempt is not None
        return change.status is R.FAILED

    repository.fail_advance_when = fail
    platform, _, handlers = three(repository, fetch=["deny"] if point == "terminal" else None)
    with pytest.raises(WorkflowUnavailableError):
        execute(platform)
    assert bool(handlers["fetch"].calls) is ran_fetch
    assert handlers["transform"].calls == []  # nothing continues past lost durability
    (run_id,) = repository.runs
    assert repository.runs[run_id]["status"] in ("pending", "running")  # resumable later


def test_malformed_stored_status_and_checkpoint_fail_closed() -> None:
    platform, repository, handlers = three(transform=["approval"])
    result = execute(platform)
    repository.runs[result.run_id]["status"] = "exploded"
    with pytest.raises(WorkflowUnavailableError):
        run(platform.resume(request(), result.run_id))
    # A corrupted checkpoint of a completed Step is never trusted on resume.
    repository2 = InMemoryWorkflowRunRepository()
    clock = StepClock()
    first = three_step_handlers()
    first["transform"].produce = lambda *a: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        execute(engine(repository2, registry(three=first), clock))
    (run_id,) = repository2.runs
    fetch_key = next(k for k in repository2.attempts if k[1] == "fetch")
    repository2.attempts[fetch_key]["checkpoint"] = {"value": "not-an-int"}
    clock.advance(3600)
    second = three_step_handlers()
    resumed = run(engine(repository2, registry(three=second), clock).resume(request(), run_id))
    assert (resumed.status, resumed.failure_code.value) == (R.FAILED, "checkpoint_invalid")
    assert second["fetch"].calls == second["transform"].calls == []


def test_a_malformed_stored_input_fails_the_resumed_run_closed() -> None:
    repository = InMemoryWorkflowRunRepository()
    clock = StepClock()
    first = three_step_handlers()
    first["fetch"].produce = lambda *a: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        execute(engine(repository, registry(three=first), clock))
    (run_id,) = repository.runs
    repository.runs[run_id]["input_state"] = {"value": "corrupted"}
    clock.advance(3600)
    second = three_step_handlers()
    result = run(engine(repository, registry(three=second), clock).resume(request(), run_id))
    assert (result.status, result.failure_code.value) == (R.FAILED, "input_invalid")
    assert second["fetch"].calls == []


# ----- no model, safe logs ---------------------------------------------------------------------


def test_execution_logs_safe_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.product.workflows")
    platform, repository, _ = three(fetch=["error", "ok"])
    result = execute(platform, value=987654)
    records = [json.loads(r.getMessage()) for r in caplog.records
               if r.name == "app.product.workflows"]  # fmt: skip
    assert records and all(set(r) == {"event", "workflow_id", "run_id", "step_id", "attempt",
                                      "status", "failure_code"} for r in records)  # fmt: skip
    assert all(r["run_id"] == str(result.run_id) for r in records)
    text = caplog.text
    assert "987654" not in text and "SENSITIVE" not in text and "fetched" not in text
