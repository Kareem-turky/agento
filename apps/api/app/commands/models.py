"""Write-command models: idempotency key, statuses, persisted records and results.

Application-facing objects (``WriteCommandRecord``, ``WriteCommandResult``) never carry
the idempotency key, its hash, the request fingerprint or raw parameters. The hashes
exist only on ``WriteCommandClaim``, the store-layer input of a claim.
"""

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictBool,
    StringConstraints,
    model_validator,
)

from app.execution.models import ActionRunReason, ActionRunStatus, SafeReference

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Opaque, case-sensitive, caller-supplied key. A header-safe vocabulary (UUIDs, ULIDs,
# base64url, dotted/colon forms). Never trimmed, lower-cased or otherwise normalized.
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:~-]{1,128}$"
IdempotencyKey = Annotated[
    str, StringConstraints(strict=True, pattern=IDEMPOTENCY_KEY_PATTERN, max_length=128)
]

Sha256Hex = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class CommandStatus(StrEnum):
    IN_PROGRESS = "in_progress"  # claimed durably; outcome not (yet) recorded
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    REQUIRES_HUMAN = "requires_human"
    VERIFIED = "verified"  # the only confirmed business success


TERMINAL_STATUSES: frozenset[CommandStatus] = frozenset(CommandStatus) - {CommandStatus.IN_PROGRESS}


class CommandReason(StrEnum):
    """Every ``ActionRunReason`` plus the command layer's own conservative reasons."""

    POLICY_DENIED = ActionRunReason.POLICY_DENIED.value
    APPROVAL_REQUIRED = ActionRunReason.APPROVAL_REQUIRED.value
    AUDIT_UNAVAILABLE = ActionRunReason.AUDIT_UNAVAILABLE.value
    HANDLER_NOT_REGISTERED = ActionRunReason.HANDLER_NOT_REGISTERED.value
    INPUT_INVALID = ActionRunReason.INPUT_INVALID.value
    HANDLER_CONTRACT_VIOLATION = ActionRunReason.HANDLER_CONTRACT_VIOLATION.value
    EXECUTION_FAILED_NO_EFFECT = ActionRunReason.EXECUTION_FAILED_NO_EFFECT.value
    EXECUTION_OUTCOME_UNCERTAIN = ActionRunReason.EXECUTION_OUTCOME_UNCERTAIN.value
    VERIFICATION_FAILED = ActionRunReason.VERIFICATION_FAILED.value
    VERIFICATION_ERROR = ActionRunReason.VERIFICATION_ERROR.value
    AUDIT_INCOMPLETE = ActionRunReason.AUDIT_INCOMPLETE.value
    VERIFIED = ActionRunReason.VERIFIED.value
    # ExecutionCoordinator raised (or broke its contract) after the durable claim.
    COMMAND_EXECUTION_ERROR = "command_execution_error"
    # The outcome could not be recorded; the durable row stays IN_PROGRESS.
    COMMAND_PERSISTENCE_INCOMPLETE = "command_persistence_incomplete"


def _check_consistent(
    status: CommandStatus,
    reason: CommandReason | None,
    action_run_id: UUID | None,
    audit_complete: bool | None,
) -> None:
    if status is CommandStatus.IN_PROGRESS:
        if reason is not None or action_run_id is not None or audit_complete is not None:
            raise ValueError("an in-progress command has no outcome")
        return
    if reason is None:
        raise ValueError("a terminal command needs a reason")
    if status is CommandStatus.VERIFIED and (
        reason is not CommandReason.VERIFIED or action_run_id is None or audit_complete is not True
    ):
        raise ValueError("a verified command needs a verified, fully audited action run")
    if reason is CommandReason.VERIFIED and status is not CommandStatus.VERIFIED:
        raise ValueError("reason 'verified' is only valid for a verified command")


class WriteCommandClaim(BaseModel):
    """Store-layer input of an atomic claim. The only model holding the hashes."""

    model_config = _FROZEN

    command_id: UUID
    company_id: str
    actor_id: str
    store_id: str | None
    action_name: str
    idempotency_key_hash: Sha256Hex
    request_fingerprint: Sha256Hex


class WriteCommandOutcome(BaseModel):
    """The terminal state written by ``WriteCommandStore.complete``."""

    model_config = _FROZEN

    status: CommandStatus
    reason: CommandReason
    action_run_id: UUID | None = None
    execution_reference_id: SafeReference | None = None
    audit_complete: StrictBool | None = None

    @model_validator(mode="after")
    def _terminal_and_consistent(self) -> Self:
        if self.status is CommandStatus.IN_PROGRESS:
            raise ValueError("an outcome is always terminal")
        _check_consistent(self.status, self.reason, self.action_run_id, self.audit_complete)
        return self


class WriteCommandRecord(BaseModel):
    """Persisted command metadata (no key, hash, fingerprint or parameters)."""

    model_config = _FROZEN

    command_id: UUID
    company_id: str
    actor_id: str
    store_id: str | None
    action_name: str
    status: CommandStatus
    reason: CommandReason | None
    action_run_id: UUID | None
    execution_reference_id: SafeReference | None
    audit_complete: StrictBool | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_consistent(self.status, self.reason, self.action_run_id, self.audit_complete)
        return self


class ClaimOutcome(StrEnum):
    NEW = "new"  # this caller owns the command; the IN_PROGRESS row is committed
    REPLAY = "replay"  # same namespace and same fingerprint: never execute
    CONFLICT = "conflict"  # same namespace, different fingerprint: never execute


class ClaimResult(BaseModel):
    """``record`` is present for NEW and REPLAY, absent for CONFLICT (nothing leaks)."""

    model_config = _FROZEN

    outcome: ClaimOutcome
    record: WriteCommandRecord | None = None

    @model_validator(mode="after")
    def _record_matches_outcome(self) -> Self:
        if (self.record is None) != (self.outcome is ClaimOutcome.CONFLICT):
            raise ValueError("NEW and REPLAY carry the command record; CONFLICT does not")
        if self.outcome is ClaimOutcome.NEW and (
            self.record is None or self.record.status is not CommandStatus.IN_PROGRESS
        ):
            raise ValueError("a NEW claim is IN_PROGRESS")
        return self


class WriteCommandResult(BaseModel):
    """What ``WriteCommandCoordinator.submit`` returns.

    ``replayed``: an existing command was returned and nothing was executed.
    ``persistence_complete``: the returned state is the durable state. False only when
    the outcome could not be recorded (the durable row then stays IN_PROGRESS).
    """

    model_config = _FROZEN

    command_id: UUID
    action_name: str
    status: CommandStatus
    reason: CommandReason | None
    action_run_id: UUID | None
    execution_reference_id: SafeReference | None
    audit_complete: StrictBool | None
    replayed: StrictBool
    persistence_complete: StrictBool


ACTION_RUN_STATUS_TO_COMMAND: dict[ActionRunStatus, CommandStatus] = {
    ActionRunStatus.DENIED: CommandStatus.DENIED,
    ActionRunStatus.AWAITING_APPROVAL: CommandStatus.AWAITING_APPROVAL,
    ActionRunStatus.FAILED: CommandStatus.FAILED,
    ActionRunStatus.REQUIRES_HUMAN: CommandStatus.REQUIRES_HUMAN,
    ActionRunStatus.VERIFIED: CommandStatus.VERIFIED,
}
