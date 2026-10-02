"""The durable Workflow state contract (Task 034), implemented by ``app.persistence``.

Every method is ONE short transaction; no transaction is ever held while Step code runs.

Execution claims (concurrency control). A run is progressed only by the executor holding
its claim: ``lease_owner`` (a fresh random token per claim) and ``lease_expires_at``.

* ``claim`` takes an active run whose claim is free or EXPIRED, rotating the token; a run
  held by a live claim is refused (``WorkflowClaimConflictError``).
* ``advance`` applies one atomic change (run fields, one attempt row inserted or finished,
  events appended) only if the run still has the caller's token AND the expected status
  (compare-and-set). A stale executor whose claim was taken over writes nothing
  (``WorkflowClaimConflictError``).

Every query is scoped by the trusted company id in the query itself. Every failure to
read or write durable state is a ``WorkflowRepositoryError`` (fixed message, never
chained): the engine never continues when durability is not guaranteed.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from app.workflow_management.records import (
    NewWorkflowEvent,
    StepAttemptRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
)
from app.workflow_management.state import (
    StepAttemptStatus,
    VerificationCode,
    WorkflowFailureCode,
    WorkflowRunStatus,
)


class WorkflowRepositoryError(Exception):
    """Durable Workflow state could not be read or written (fixed message)."""

    def __init__(self) -> None:
        super().__init__("workflow state unavailable")


class WorkflowClaimConflictError(Exception):
    """The run is claimed by another live executor, or this executor's claim is stale."""

    def __init__(self) -> None:
        super().__init__("workflow run claimed elsewhere")


class Claim(BaseModel):
    """The caller's execution claim on one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    company_id: str
    token: UUID


class AttemptFinish(BaseModel):
    """Finishes the RUNNING attempt ``(step_id, attempt)`` of the claimed run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    attempt: int
    status: StepAttemptStatus
    failure_code: WorkflowFailureCode | None = None
    verification_code: VerificationCode | None = None
    checkpoint: dict[str, JsonValue] | None = None
    completed_at: datetime


class RunChange(BaseModel):
    """One atomic change of a claimed run (compare-and-set on token + status)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_status: WorkflowRunStatus
    status: WorkflowRunStatus
    current_step_id: str | None
    failure_code: WorkflowFailureCode | None = None
    lease_expires_at: datetime | None  # None: release the claim (terminal runs)
    updated_at: datetime
    completed_at: datetime | None = None
    start_attempt: StepAttemptRecord | None = None
    finish_attempt: AttemptFinish | None = None
    events: tuple[NewWorkflowEvent, ...] = ()


@runtime_checkable
class WorkflowRunRepository(Protocol):
    async def create_run(self, run: WorkflowRunRecord, event: NewWorkflowEvent) -> None:
        """Insert a new ``pending`` run and its ``workflow_requested`` event."""
        ...

    async def claim(
        self, company_id: str, run_id: UUID, token: UUID, now: datetime, lease_expires_at: datetime
    ) -> WorkflowRunRecord:
        """Claim an active run (free or expired claim); raises ``LookupError`` if no such
        run exists for this company, ``WorkflowClaimConflictError`` if it is held or not
        active."""
        ...

    async def advance(self, claim: Claim, change: RunChange) -> WorkflowRunRecord: ...

    async def get_run(self, company_id: str, run_id: UUID) -> WorkflowRunRecord | None: ...

    async def list_runs(
        self, company_id: str, limit: int
    ) -> tuple[tuple[WorkflowRunRecord, int], ...]:
        """The company's most recent runs (newest first) with their attempt counts."""
        ...

    async def list_attempts(
        self, company_id: str, run_id: UUID
    ) -> tuple[StepAttemptRecord, ...]: ...

    async def list_events(
        self, company_id: str, run_id: UUID
    ) -> tuple[WorkflowEventRecord, ...]: ...
