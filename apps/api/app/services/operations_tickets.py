"""The product-owned Operations ticket command service contract.

``POST /api/v1/operations/tickets`` depends on this narrow interface only: never on
the command layer, persistence, execution, handlers, integrations or an agent
runtime. ``app.application.operations_tickets`` implements it on top of the durable
``WriteCommandCoordinator``.

The result is a safe, self-consistent projection of a durable command:

- ``status`` uses the command vocabulary; VERIFIED is the only confirmed creation;
- ``reason`` is a fixed safe code (never free text);
- ``ticket_id`` (the canonical ticket UUID) is present if and only if VERIFIED;
- ``persistence_complete`` is False only for REQUIRES_HUMAN /
  command_persistence_incomplete (the durable command then stays IN_PROGRESS).

Nothing here carries the idempotency key or its hash, the request fingerprint, raw
parameters, action runs, audit data, policy decisions or provider identifiers.
"""

from enum import StrEnum
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from app.context.models import RequestContext
from app.governance import ActionScope


class TicketCommandStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    REQUIRES_HUMAN = "requires_human"
    VERIFIED = "verified"


class TicketCommandReason(StrEnum):
    """The only reason codes that may cross the Product API boundary."""

    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    AUDIT_UNAVAILABLE = "audit_unavailable"
    HANDLER_NOT_REGISTERED = "handler_not_registered"
    INPUT_INVALID = "input_invalid"
    HANDLER_CONTRACT_VIOLATION = "handler_contract_violation"
    EXECUTION_FAILED_NO_EFFECT = "execution_failed_no_effect"
    EXECUTION_OUTCOME_UNCERTAIN = "execution_outcome_uncertain"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_ERROR = "verification_error"
    AUDIT_INCOMPLETE = "audit_incomplete"
    VERIFIED = "verified"
    COMMAND_EXECUTION_ERROR = "command_execution_error"
    COMMAND_PERSISTENCE_INCOMPLETE = "command_persistence_incomplete"


class ProductTicketCommandResult(BaseModel):
    """Safe outcome of one durable ticket command. Invalid combinations are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: UUID
    status: TicketCommandStatus
    reason: TicketCommandReason | None
    ticket_id: UUID | None
    replayed: StrictBool
    persistence_complete: StrictBool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        S, R = TicketCommandStatus, TicketCommandReason
        verified = self.status is S.VERIFIED
        if verified != (self.ticket_id is not None):
            raise ValueError("ticket_id is present if and only if the command is verified")
        if verified != (self.reason is R.VERIFIED):
            raise ValueError("reason 'verified' belongs to a verified command only")
        if (self.status is S.IN_PROGRESS) != (self.reason is None):
            raise ValueError("only an in-progress command has no reason")
        incomplete = self.reason is R.COMMAND_PERSISTENCE_INCOMPLETE
        if incomplete == self.persistence_complete:
            raise ValueError("persistence_complete is False exactly for incomplete persistence")
        if incomplete and (self.status is not S.REQUIRES_HUMAN or self.replayed):
            raise ValueError("incomplete persistence is a fresh requires_human result")
        return self


class OperationsTicketCommandError(Exception):
    """Base of the safe service errors. Messages are fixed codes, never details."""

    code = "ticket_command_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidIdempotencyKeyError(OperationsTicketCommandError):
    code = "idempotency_key_invalid"


class IdempotencyConflictError(OperationsTicketCommandError):
    """The key was already used for a different request. Nothing was executed."""

    code = "idempotency_conflict"


class TicketCommandUnavailableError(OperationsTicketCommandError):
    code = "ticket_command_unavailable"


@runtime_checkable
class OperationsTicketCommandService(Protocol):
    async def create_ticket(
        self,
        request: RequestContext,
        scope: ActionScope,
        title: str,
        description: str,
        idempotency_key: str,
    ) -> ProductTicketCommandResult:
        """Create one operational ticket at most once per idempotency key.

        ``request`` and ``scope`` are trusted; ``title``, ``description`` and the key
        are untrusted. Raises only ``OperationsTicketCommandError`` subclasses.
        """
        ...
