"""Structural, metadata-only audit events and the audit sink contract.

Events never carry raw parameters, raw provider responses, prompts, secrets or
exception messages; only identifiers, typed outcomes and safe reference ids.
There is no persistence here and no global sink: the coordinator is given one.
"""

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.context.models import ActorType, Channel
from app.execution.models import ActionRunReason, ActionRunStatus, ReasonCode, SafeReference
from app.governance import PolicyOutcome, PolicyReason


class AuditEventType(StrEnum):
    REQUESTED = "requested"
    POLICY_DECIDED = "policy_decided"
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    HANDLER_NOT_REGISTERED = "handler_not_registered"
    VALIDATION_FAILED = "validation_failed"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    VERIFICATION_STARTED = "verification_started"
    VERIFIED = "verified"
    REQUIRES_HUMAN = "requires_human"


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    run_id: UUID
    request_id: UUID
    occurred_at: AwareDatetime
    event_type: AuditEventType
    action_name: str
    actor_id: str | None
    actor_type: ActorType | None
    company_id: str
    store_id: str | None
    channel: Channel
    policy_outcome: PolicyOutcome | None = None
    policy_reason: PolicyReason | None = None
    run_status: ActionRunStatus | None = None
    run_reason: ActionRunReason | None = None
    execution_reference_id: SafeReference | None = None
    verification_code: ReasonCode | None = None


class AuditSink(Protocol):
    """Records one event. Raising means the write failed; the coordinator decides."""

    async def record(self, event: AuditEvent) -> None: ...
