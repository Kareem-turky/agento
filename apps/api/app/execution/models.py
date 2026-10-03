"""Immutable execution contracts: results, verification and the terminal ActionRun."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints

from app.governance import PolicyDecision

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Safe, stable identifiers only: never free text, raw provider payloads or messages.
SafeReference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]


class ActionRunStatus(StrEnum):
    DENIED = "denied"  # governance denied it
    AWAITING_APPROVAL = "awaiting_approval"  # a human decision is pending; nothing executed
    FAILED = "failed"  # stopped safely with no side effect
    REQUIRES_HUMAN = "requires_human"  # a side effect may exist but is not confirmed
    VERIFIED = "verified"  # executed and independently verified, fully audited


class ActionRunReason(StrEnum):
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    AUDIT_UNAVAILABLE = "audit_unavailable"  # a required pre-execution audit write failed
    HANDLER_NOT_REGISTERED = "handler_not_registered"
    INPUT_INVALID = "input_invalid"
    HANDLER_CONTRACT_VIOLATION = "handler_contract_violation"
    EXECUTION_FAILED_NO_EFFECT = "execution_failed_no_effect"
    EXECUTION_OUTCOME_UNCERTAIN = "execution_outcome_uncertain"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_ERROR = "verification_error"
    AUDIT_INCOMPLETE = "audit_incomplete"  # verified, but the audit trail is incomplete
    VERIFIED = "verified"
    # Task 036: a human decision was required and could not authorize this execution.
    APPROVAL_UNAVAILABLE = "approval_unavailable"  # no request could be recorded / used
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_NOT_DECIDED = "approval_not_decided"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_MISMATCH = "approval_mismatch"
    APPROVAL_ALREADY_CONSUMED = "approval_already_consumed"


class ExecutionResult(BaseModel):
    """What a handler returns from ``execute``: a safe receipt, never a provider payload."""

    model_config = _FROZEN

    reference_id: SafeReference | None = None


class VerificationResult(BaseModel):
    """Independent confirmation (a re-read of the external system) of the intended effect."""

    model_config = _FROZEN

    verified: StrictBool
    reason_code: ReasonCode


class ActionRun(BaseModel):
    model_config = _FROZEN

    run_id: UUID
    request_id: UUID
    action_name: str
    status: ActionRunStatus
    reason: ActionRunReason
    # None only when the run stopped before governance (the first audit write failed).
    policy_decision: PolicyDecision | None
    execution_result: ExecutionResult | None = None
    verification_result: VerificationResult | None = None
    audit_complete: StrictBool
    # Task 036: the human-approval request this run is awaiting (AWAITING_APPROVAL) or
    # executed under (a consumed request). None when the action never needed one.
    approval_id: UUID | None = None
