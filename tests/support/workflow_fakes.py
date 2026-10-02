"""TEST-ONLY Workflow Platform doubles (never used by production code).

* ``InMemoryWorkflowRunRepository``: the ``WorkflowRunRepository`` contract with the SAME
  claim/compare-and-set semantics as the PostgreSQL implementation. Rows are kept as raw
  dicts and rebuilt through the domain records on every read (corrupt rows fail closed),
  and any operation can be told to fail (``fail_on``) to prove the engine never continues
  without durable state.
* TEST-ONLY Workflows and deterministic Step handlers that prove the generic platform:
  ``testing.three_steps`` (three read-only Steps passing checkpoints, with retries) and
  ``testing.governed_write`` (a governed write through the REAL ExecutionCoordinator).
  They are never part of the production catalog.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from app.context.models import ActorContext, RequestContext
from app.governance import ActionIntent, ActionScope
from app.workflow_management.catalog import ProductWorkflowCatalog
from app.workflow_management.contracts import (
    Claim,
    RunChange,
    WorkflowClaimConflictError,
    WorkflowRepositoryError,
)
from app.workflow_management.definitions import (
    CheckpointPolicy,
    StepSideEffect,
    WorkflowCategory,
    WorkflowDefinition,
    WorkflowInputField,
    WorkflowInputKind,
    WorkflowStepDefinition,
)
from app.workflow_management.engine import WorkflowEngine
from app.workflow_management.handlers import (
    Checkpoints,
    GovernedWriteStepHandler,
    ReadOnlyStepHandler,
    StepDeniedError,
    StepFailedError,
    StepOutcome,
    StepResult,
    WorkflowRuntimeRegistration,
    WorkflowRuntimeRegistry,
    WorkflowStepContext,
)
from app.workflow_management.records import (
    NewWorkflowEvent,
    StepAttemptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)
from app.workflow_management.state import ACTIVE_RUN_STATUSES, StepAttemptStatus

COMPANY = "company-1"
OTHER_COMPANY = "company-2"
STORE = "store-a"
T0 = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)


class StepClock:
    """Aware, strictly increasing test time; ``advance`` jumps (lease expiry tests)."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


# ----- in-memory repository ------------------------------------------------------------------


class InMemoryWorkflowRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, dict[str, Any]] = {}
        self.attempts: dict[tuple[UUID, str, int], dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.fail_on: set[str] = set()
        self.fail_advance_when: Any = None  # callable(RunChange) -> bool
        self.calls: list[str] = []

    def _maybe_fail(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.fail_on:
            raise WorkflowRepositoryError()

    @staticmethod
    def _record[T](model: type[T], data: Mapping[str, Any]) -> T:
        try:
            return model.model_validate(dict(data))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - corrupt stored data fails closed
            raise WorkflowRepositoryError() from None

    def _append(self, run_id: UUID, company_id: str, events: tuple[NewWorkflowEvent, ...]) -> None:
        last = max((e["sequence"] for e in self.events if e["run_id"] == run_id), default=0)
        for index, event in enumerate(events, start=1):
            self.events.append({"run_id": run_id, "company_id": company_id,
                                "sequence": last + index,
                                **event.model_dump(mode="json")})  # fmt: skip

    async def create_run(self, run: WorkflowRunRecord, event: NewWorkflowEvent) -> None:
        self._maybe_fail("create_run")
        if run.run_id in self.runs:
            raise WorkflowRepositoryError()
        self.runs[run.run_id] = run.model_dump(mode="json")
        self._append(run.run_id, run.company_id, (event,))

    async def claim(
        self, company_id: str, run_id: UUID, token: UUID, now: datetime, lease_expires_at: datetime
    ) -> WorkflowRunRecord:
        self._maybe_fail("claim")
        row = self.runs.get(run_id)
        if row is None or row["company_id"] != company_id:
            raise LookupError("workflow run not found")
        record = self._record(WorkflowRunRecord, row)
        free = record.lease_owner is None or (
            record.lease_expires_at is not None and record.lease_expires_at <= now
        )
        if record.status not in ACTIVE_RUN_STATUSES or not free:
            raise WorkflowClaimConflictError()
        row.update(lease_owner=str(token), lease_expires_at=lease_expires_at.isoformat())
        return self._record(WorkflowRunRecord, row)

    async def advance(self, claim: Claim, change: RunChange) -> WorkflowRunRecord:
        self._maybe_fail("advance")
        if self.fail_advance_when is not None and self.fail_advance_when(change):
            raise WorkflowRepositoryError()
        row = self.runs.get(claim.run_id)
        if (
            row is None
            or row["company_id"] != claim.company_id
            or row["lease_owner"] != str(claim.token)
            or row["status"] != change.expected_status.value
        ):
            raise WorkflowClaimConflictError()
        updated = dict(row)
        updated.update(
            status=change.status.value, current_step_id=change.current_step_id,
            failure_code=None if change.failure_code is None else change.failure_code.value,
            updated_at=change.updated_at.isoformat(),
            completed_at=None if change.completed_at is None else change.completed_at.isoformat(),
            lease_expires_at=None if change.lease_expires_at is None
            else change.lease_expires_at.isoformat(),
            lease_owner=str(claim.token) if change.lease_expires_at is not None else None,
        )  # fmt: skip
        record = self._record(WorkflowRunRecord, updated)  # the DB CHECKs, as validation
        attempts = dict(self.attempts)
        if change.start_attempt is not None:
            key = (claim.run_id, change.start_attempt.step_id, change.start_attempt.attempt)
            if key in attempts:
                raise WorkflowRepositoryError()
            attempts[key] = change.start_attempt.model_dump(mode="json")
        if change.finish_attempt is not None:
            finish = change.finish_attempt
            key = (claim.run_id, finish.step_id, finish.attempt)
            current = attempts.get(key)
            if current is None or current["status"] != StepAttemptStatus.RUNNING.value:
                raise WorkflowRepositoryError()
            done = dict(current)
            done.update(finish.model_dump(mode="json", exclude={"step_id", "attempt"}))
            self._record(StepAttemptRecord, done)
            attempts[key] = done
        # Commit atomically.
        self.runs[claim.run_id] = updated
        self.attempts = attempts
        self._append(claim.run_id, claim.company_id, change.events)
        return record

    async def get_run(self, company_id: str, run_id: UUID) -> WorkflowRunRecord | None:
        self._maybe_fail("get_run")
        row = self.runs.get(run_id)
        if row is None or row["company_id"] != company_id:
            return None
        return self._record(WorkflowRunRecord, row)

    async def list_runs(
        self, company_id: str, limit: int
    ) -> tuple[tuple[WorkflowRunRecord, int], ...]:
        self._maybe_fail("list_runs")
        rows = [r for r in self.runs.values() if r["company_id"] == company_id]
        rows.sort(key=lambda r: (r["created_at"], r["run_id"]), reverse=True)
        return tuple(
            (self._record(WorkflowRunRecord, r),
             sum(1 for k in self.attempts if str(k[0]) == r["run_id"]))
            for r in rows[:limit]
        )  # fmt: skip

    async def list_attempts(self, company_id: str, run_id: UUID) -> tuple[StepAttemptRecord, ...]:
        self._maybe_fail("list_attempts")
        rows = [a for k, a in self.attempts.items()
                if k[0] == run_id and a["company_id"] == company_id]  # fmt: skip
        rows.sort(key=lambda a: (a["started_at"], a["step_id"], a["attempt"]))
        return tuple(self._record(StepAttemptRecord, a) for a in rows)

    async def list_events(self, company_id: str, run_id: UUID) -> tuple[WorkflowEventRecord, ...]:
        self._maybe_fail("list_events")
        rows = [e for e in self.events if e["run_id"] == run_id and e["company_id"] == company_id]
        rows.sort(key=lambda e: e["sequence"])
        return tuple(self._record(WorkflowEventRecord, e) for e in rows)

    # -- helpers for assertions --------------------------------------------------------------

    def event_types(self, run_id: UUID) -> list[str]:
        return [e["event_type"] for e in sorted(self.events, key=lambda e: e["sequence"])
                if e["run_id"] == run_id]  # fmt: skip

    def attempt_rows(self, run_id: UUID) -> list[dict[str, Any]]:
        return sorted((a for k, a in self.attempts.items() if k[0] == run_id),
                      key=lambda a: (a["started_at"], a["attempt"]))  # fmt: skip


# ----- TEST-ONLY workflows ---------------------------------------------------------------------

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class TestInput(BaseModel):
    __test__ = False
    model_config = _FROZEN
    value: int


class Fetched(BaseModel):
    model_config = _FROZEN
    value: int


class Transformed(BaseModel):
    model_config = _FROZEN
    doubled: int


class Prepared(BaseModel):
    model_config = _FROZEN
    note: str


READ = StepSideEffect.READ_ONLY


def _step(step_id: str, handler_id: str, *, attempts: int = 1, timeout: int = 2,
          checkpoint: bool = False, side_effect: StepSideEffect = READ,
          ) -> WorkflowStepDefinition:  # fmt: skip
    return WorkflowStepDefinition(
        step_id=step_id, name=step_id.title(), description=f"Test step {step_id}.",
        handler_id=handler_id, side_effect=side_effect, timeout_seconds=timeout,
        max_attempts=attempts,
        checkpoint_policy=CheckpointPolicy.STATE if checkpoint else CheckpointPolicy.NONE,
    )  # fmt: skip


THREE_STEPS = WorkflowDefinition(
    workflow_id="testing.three_steps", name="Three steps", description="TEST ONLY.",
    category=WorkflowCategory.OPERATIONS, version=1,
    inputs=(WorkflowInputField(name="value", label="Value", kind=WorkflowInputKind.INTEGER,
                               required=True, description="A number."),),
    steps=(
        _step("fetch", "testing.fetch", attempts=3, timeout=1, checkpoint=True),
        _step("transform", "testing.transform", attempts=2, checkpoint=True),
        _step("finish", "testing.finish"),
    ),
)  # fmt: skip
GOVERNED_WRITE = WorkflowDefinition(
    workflow_id="testing.governed_write", name="Governed write", description="TEST ONLY.",
    category=WorkflowCategory.OPERATIONS, version=1,
    steps=(
        _step("prepare", "testing.prepare", checkpoint=True),
        _step("write", "testing.write_note", timeout=1, checkpoint=True,
              side_effect=StepSideEffect.GOVERNED_WRITE),
        _step("after", "testing.after"),
    ),
)  # fmt: skip
TEST_CATALOG = ProductWorkflowCatalog([THREE_STEPS, GOVERNED_WRITE])


class Scripted(ReadOnlyStepHandler):
    """A deterministic read-only Step. ``script`` holds one behaviour per attempt (the last
    one repeats): ok | error | fail | retryable | deny | timeout | unverified |
    verify_error | bad_checkpoint | big_checkpoint | approval | human | bad_result."""

    checkpoint_model: Any = None

    def __init__(self, handler_id: str, checkpoint_model: type[BaseModel] | None,
                 produce: Any, script: list[str] | None = None) -> None:  # fmt: skip
        self.handler_id = handler_id  # type: ignore[misc]
        self.checkpoint_model = checkpoint_model
        self.produce = produce
        self.script = list(script or ["ok"])
        self.calls: list[tuple[int, dict[str, BaseModel]]] = []
        self.contexts: list[WorkflowStepContext] = []

    def _behaviour(self) -> str:
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]

    async def execute(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> StepResult:
        self.calls.append((context.attempt, dict(checkpoints)))
        self.contexts.append(context)
        behaviour = self._behaviour()
        match behaviour:
            case "error":
                raise RuntimeError("SENSITIVE-step-error-text")
            case "fail":
                raise StepFailedError()
            case "retryable":
                raise StepFailedError(retryable=True)
            case "deny":
                raise StepDeniedError()
            case "timeout":
                await asyncio.sleep(30)
            case "approval":
                return StepResult(StepOutcome.AWAITING_APPROVAL)
            case "human":
                return StepResult(StepOutcome.REQUIRES_HUMAN)
            case "bad_result":
                return {"ok": True}  # type: ignore[return-value]
            case "bad_checkpoint":
                return StepResult(StepOutcome.COMPLETED, checkpoint=Prepared(note="wrong type"),
                                  output="out")  # fmt: skip
            case "big_checkpoint":
                return StepResult(StepOutcome.COMPLETED, checkpoint=Prepared(note="x" * 9000))
        checkpoint, output = self.produce(run_input, checkpoints)
        return StepResult(StepOutcome.COMPLETED, checkpoint=checkpoint, output=output)

    async def verify(
        self,
        context: WorkflowStepContext,
        run_input: BaseModel,
        checkpoints: Checkpoints,
        result: StepResult,
    ) -> bool:
        behaviour = self._behaviour()
        if behaviour == "unverified":
            return False
        if behaviour == "verify_error":
            raise RuntimeError("SENSITIVE-verify-error-text")
        return True


def three_step_handlers(fetch: list[str] | None = None, transform: list[str] | None = None,
                        finish: list[str] | None = None) -> dict[str, Scripted]:  # fmt: skip
    return {
        "fetch": Scripted("testing.fetch", Fetched,
                          lambda i, c: (Fetched(value=i.value), {"fetched": i.value}), fetch),
        "transform": Scripted("testing.transform", Transformed,
                              lambda i, c: (Transformed(doubled=c["fetch"].value * 2), None),
                              transform),
        "finish": Scripted("testing.finish", None,
                           lambda i, c: (None, {"result": c["transform"].doubled}), finish),
    }  # fmt: skip


class WriteNote(GovernedWriteStepHandler):
    """A governed write Step (TEST ONLY): the note is written by the coordinator's
    ``notes.add`` handler, never by this Step."""

    handler_id = "testing.write_note"
    intent_name = "notes.add"  # "orders.cancel" (medium risk) requires approval

    def action(
        self, context: WorkflowStepContext, run_input: BaseModel, checkpoints: Checkpoints
    ) -> tuple[ActionIntent, Mapping[str, JsonValue]]:
        prepared = checkpoints["prepare"]
        assert isinstance(prepared, Prepared)
        return ActionIntent(name=self.intent_name), {"order_ref": "ord-1", "text": prepared.note}


class EmptyInput(BaseModel):
    model_config = _FROZEN


def write_handlers(coordinator: Any, prepare: list[str] | None = None,
                   after: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "prepare": Scripted("testing.prepare", Prepared,
                            lambda i, c: (Prepared(note="Customer called"), None), prepare),
        "write": WriteNote(coordinator),
        "after": Scripted("testing.after", None, lambda i, c: (None, "done"), after),
    }  # fmt: skip


def registry(three: dict[str, Any] | None = None,
             write: dict[str, Any] | None = None) -> WorkflowRuntimeRegistry:  # fmt: skip
    registrations = []
    if three is not None:
        registrations.append(WorkflowRuntimeRegistration(
            "testing.three_steps", TestInput, tuple(three.values())))  # fmt: skip
    if write is not None:
        registrations.append(WorkflowRuntimeRegistration(
            "testing.governed_write", EmptyInput, tuple(write.values())))  # fmt: skip
    return WorkflowRuntimeRegistry(TEST_CATALOG, registrations)


def engine(repository: Any, bindings: WorkflowRuntimeRegistry, clock: Any = None,
           **kwargs: Any) -> WorkflowEngine:  # fmt: skip
    return WorkflowEngine(TEST_CATALOG, bindings, repository, clock=clock or StepClock(),
                          **kwargs)  # fmt: skip


def actor(company_id: str = COMPANY, **overrides: Any) -> ActorContext:
    data: dict[str, Any] = {"actor_id": "user-1", "actor_type": "user", "company_id": company_id,
                            "permissions": frozenset({"notes.add", "orders.cancel"}),
                            "store_ids": frozenset({STORE})}  # fmt: skip
    data.update(overrides)
    return ActorContext(**data)


def request(company_id: str = COMPANY, **overrides: Any) -> RequestContext:
    return RequestContext(actor=actor(company_id, **overrides))


def scope(company_id: str = COMPANY, store_id: str | None = STORE) -> ActionScope:
    return ActionScope(company_id=company_id, store_id=store_id)
