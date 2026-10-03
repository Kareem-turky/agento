"""Durable human-approval records (Task 036).

``ApprovalRequest`` is the current state of one request; ``ApprovalEvent`` is its
append-only history. Neither holds raw action parameters, a request body, a provider
payload, model text or a credential: only the subject FINGERPRINT (what execution binds
to), the trusted safe ``ApprovalSummary`` (what a human reads), safe identifiers and the
bounded human decision note (inert audit text: never executed, never an instruction).
"""

import unicodedata
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.approval_management.errors import ApprovalInputError, ApprovalInputReason
from app.approval_management.state import ApprovalEventType, ApprovalStatus
from app.context.models import ActorType
from app.execution.approvals import ApprovalOutcome, ApprovalSourceRef, ApprovalSummary
from app.governance import ActionRisk

_FROZEN = ConfigDict(frozen=True, extra="forbid")
MAX_NOTE_CHARS = 1000

ScopedId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ActionName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
Fingerprint = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
DecisionNote = Annotated[str, StringConstraints(strict=True, min_length=1,
                                                max_length=MAX_NOTE_CHARS)]  # fmt: skip
APPROVAL_RISKS = frozenset({ActionRisk.MEDIUM_RISK, ActionRisk.HIGH_RISK})


def normalize_note(note: object, *, required: bool) -> str | None:
    """A bounded, printable human note (or None). Never interpreted."""
    if note is None or (isinstance(note, str) and not note.strip()):
        if required:
            raise ApprovalInputError(ApprovalInputReason.NOTE_REQUIRED)
        return None
    if not isinstance(note, str):
        raise ApprovalInputError(ApprovalInputReason.NOTE_INVALID)
    text = note.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_NOTE_CHARS or any(
        unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in text
    ):
        raise ApprovalInputError(ApprovalInputReason.NOTE_INVALID)
    return text


class ApprovalRequest(BaseModel):
    model_config = _FROZEN

    approval_id: UUID
    company_id: ScopedId
    store_id: ScopedId | None
    action_name: ActionName
    risk: ActionRisk
    requester_actor_id: ScopedId
    requester_actor_type: ActorType
    request_id: UUID
    action_run_id: UUID  # the run that asked for the decision
    subject_fingerprint: Fingerprint
    status: ApprovalStatus
    summary: ApprovalSummary
    source: ApprovalSourceRef
    created_at: AwareDatetime
    expires_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    decided_by_actor_id: ScopedId | None = None
    decided_by_actor_type: ActorType | None = None
    decision_note: DecisionNote | None = None
    consumed_at: AwareDatetime | None = None
    consumed_by_action_run_id: UUID | None = None
    execution_outcome: ApprovalOutcome | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.risk not in APPROVAL_RISKS:
            raise ValueError("only MEDIUM_RISK and HIGH_RISK actions need a human decision")
        if self.expires_at <= self.created_at:
            raise ValueError("an approval expires after it was requested")
        requested = self.status is ApprovalStatus.REQUESTED
        if requested != (self.decided_at is None):
            raise ValueError("decided_at is set exactly for decided requests")
        human = self.status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
                                ApprovalStatus.CANCELLED)  # fmt: skip
        if human != (self.decided_by_actor_id is not None):
            raise ValueError("a human decision names its decider; expiry names none")
        if (self.decided_by_actor_id is None) != (self.decided_by_actor_type is None):
            raise ValueError("inconsistent decider")
        if human and self.decided_by_actor_type == "system_agent":
            raise ValueError("an Agent never decides a human approval")
        if self.status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED) and (
            self.decided_by_actor_id == self.requester_actor_id
        ):
            raise ValueError("a requester never decides their own request")
        if self.status in (ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED) and (
            self.decision_note is None
        ):
            raise ValueError("a rejection or cancellation has a reason")
        if self.status is not ApprovalStatus.APPROVED and self.consumed_at is not None:
            raise ValueError("only an approved request can be consumed")
        if (self.consumed_at is None) != (self.consumed_by_action_run_id is None):
            raise ValueError("inconsistent consumption")
        if self.execution_outcome is not None and self.consumed_at is None:
            raise ValueError("an execution outcome needs a consumed request")
        return self

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None


class ApprovalEvent(BaseModel):
    """One append-only lifecycle fact (safe metadata only: no note, no summary)."""

    model_config = _FROZEN

    approval_id: UUID
    company_id: ScopedId
    sequence: int = Field(ge=1)
    event_type: ApprovalEventType
    status: ApprovalStatus
    actor_id: ScopedId | None = None
    actor_type: ActorType | None = None
    action_run_id: UUID | None = None
    execution_outcome: ApprovalOutcome | None = None
    occurred_at: AwareDatetime
