"""Durable Workflow execution records (Task 034): CONTROL state only.

These are the rows of ``product.workflow_runs``, ``product.workflow_step_runs`` and
``product.workflow_events``. They carry what execution control, recovery, audit and
observability need, and nothing else: no business data (orders, shipments, reports…),
provider payload, credential, model text or exception message.

* ``input_state`` is the run's VALIDATED typed input (the registered input model, at most
  ``MAX_INPUT_BYTES``), kept only so an interrupted run can be resumed. It is never
  returned by the inspection API, logged or written to an event.
* ``checkpoint`` is the typed, size-bounded state a Step hands to later Steps
  (``MAX_CHECKPOINT_BYTES``). Never returned by the inspection API, logged or evented.
* ``lease_owner``/``lease_expires_at`` are the execution claim (see the engine).

Every record is rebuilt strictly from storage: an invalid stored value fails closed.
"""

import json
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from app.context.models import ActorType, Channel
from app.workflow_management.definitions import HandlerId, StepId, WorkflowId
from app.workflow_management.state import (
    TERMINAL_RUN_STATUSES,
    StepAttemptStatus,
    VerificationCode,
    WorkflowEventType,
    WorkflowFailureCode,
    WorkflowRunStatus,
)

_FROZEN = ConfigDict(frozen=True, extra="forbid")
MAX_INPUT_BYTES = 4096
MAX_CHECKPOINT_BYTES = 8192
MAX_STEP_ATTEMPT_NUMBER = 20
CompanyId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ScopeId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
# The run or attempt status an event reports (the union of both vocabularies).
EVENT_STATUSES = tuple(sorted({*WorkflowRunStatus, *StepAttemptStatus}))
EventStatus = Annotated[
    str, StringConstraints(strict=True, pattern="^(" + "|".join(EVENT_STATUSES) + ")$")
]
Fingerprint = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


def canonical_json(value: Any) -> str:
    """Deterministic compact JSON (sorted keys): used for sizes and fingerprints."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bounded_object(value: Any, limit: int) -> None:
    if not isinstance(value, dict):
        raise ValueError("state must be a JSON object")
    if len(canonical_json(value).encode()) > limit:
        raise ValueError("state exceeds its size limit")


class WorkflowRunRecord(BaseModel):
    model_config = _FROZEN

    run_id: UUID
    workflow_id: WorkflowId
    workflow_version: int = Field(ge=1, le=10_000)
    request_id: UUID
    company_id: CompanyId
    actor_id: ScopeId
    actor_type: ActorType
    channel: Channel
    store_id: ScopeId | None
    status: WorkflowRunStatus
    current_step_id: StepId | None
    failure_code: WorkflowFailureCode | None
    input_state: dict[str, JsonValue]
    input_fingerprint: Fingerprint
    lease_owner: UUID | None
    lease_expires_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None

    @model_validator(mode="after")
    def _consistent(self) -> "WorkflowRunRecord":
        _bounded_object(self.input_state, MAX_INPUT_BYTES)
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("inconsistent execution claim")
        terminal = self.status in TERMINAL_RUN_STATUSES
        if terminal != (self.completed_at is not None):
            raise ValueError("completed_at must be set exactly for terminal runs")
        if terminal and self.lease_owner is not None:
            raise ValueError("a terminal run holds no execution claim")
        if self.status is WorkflowRunStatus.SUCCEEDED and self.failure_code is not None:
            raise ValueError("a succeeded run has no failure code")
        if self.status is WorkflowRunStatus.FAILED and self.failure_code is None:
            raise ValueError("a failed run has a failure code")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at precedes created_at")
        return self


class StepAttemptRecord(BaseModel):
    model_config = _FROZEN

    run_id: UUID
    company_id: CompanyId
    step_id: StepId
    attempt: int = Field(ge=1, le=MAX_STEP_ATTEMPT_NUMBER)
    handler_id: HandlerId
    status: StepAttemptStatus
    failure_code: WorkflowFailureCode | None
    verification_code: VerificationCode | None
    checkpoint: dict[str, JsonValue] | None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None
    # Task 036: the human-approval request this governed write attempt awaited (status
    # awaiting_approval) or executed under (the continuation attempt). Never parameters.
    approval_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "StepAttemptRecord":
        running = self.status is StepAttemptStatus.RUNNING
        if running != (self.completed_at is None):
            raise ValueError("completed_at must be set exactly for finished attempts")
        if self.checkpoint is not None:
            _bounded_object(self.checkpoint, MAX_CHECKPOINT_BYTES)
            if self.status is not StepAttemptStatus.SUCCEEDED:
                raise ValueError("only a succeeded attempt carries a checkpoint")
        if self.status is StepAttemptStatus.SUCCEEDED and (
            self.failure_code is not None or self.verification_code is not VerificationCode.VERIFIED
        ):
            raise ValueError("a succeeded attempt is verified and has no failure code")
        if self.status in (StepAttemptStatus.FAILED, StepAttemptStatus.TIMED_OUT) and (
            self.failure_code is None
        ):
            raise ValueError("a failed attempt has a failure code")
        return self


class WorkflowEventRecord(BaseModel):
    """One append-only Workflow lifecycle event: safe metadata only."""

    model_config = _FROZEN

    run_id: UUID
    company_id: CompanyId
    sequence: int = Field(ge=1)
    event_type: WorkflowEventType
    step_id: StepId | None = None
    attempt: int | None = Field(default=None, ge=1, le=MAX_STEP_ATTEMPT_NUMBER)
    status: EventStatus | None = None
    failure_code: WorkflowFailureCode | None = None
    occurred_at: AwareDatetime


class NewWorkflowEvent(BaseModel):
    """An event to append; the repository assigns the run-local sequence number."""

    model_config = _FROZEN

    event_type: WorkflowEventType
    step_id: StepId | None = None
    attempt: int | None = Field(default=None, ge=1, le=MAX_STEP_ATTEMPT_NUMBER)
    status: EventStatus | None = None
    failure_code: WorkflowFailureCode | None = None
    occurred_at: AwareDatetime
