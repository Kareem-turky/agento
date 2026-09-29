"""Durable write commands: PostgreSQL-backed idempotency above governed execution.

Generic product core. Depends only on the standard library, Pydantic,
``app.context.models``, ``app.governance`` and ``app.execution``; it knows nothing
about SQLAlchemy, FastAPI, Agno, Redis, integrations or business handlers. The
durable store is injected (``WriteCommandStore``); ``app.persistence`` implements it.
"""

from app.commands.coordinator import WriteCommandCoordinator
from app.commands.errors import (
    AnonymousWriteCommandError,
    IdempotencyConflictError,
    InvalidCommandParametersError,
    InvalidIdempotencyKeyError,
    ReadActionNotAllowedError,
    UnknownWriteActionError,
    WriteCommandError,
    WriteCommandStoreError,
)
from app.commands.fingerprint import (
    canonical_json,
    hash_idempotency_key,
    request_fingerprint,
    snapshot_parameters,
)
from app.commands.models import (
    ACTION_RUN_STATUS_TO_COMMAND,
    IDEMPOTENCY_KEY_PATTERN,
    TERMINAL_STATUSES,
    ClaimOutcome,
    ClaimResult,
    CommandReason,
    CommandStatus,
    IdempotencyKey,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandRecord,
    WriteCommandResult,
)
from app.commands.store import WriteCommandStore

__all__ = [
    "ACTION_RUN_STATUS_TO_COMMAND",
    "IDEMPOTENCY_KEY_PATTERN",
    "TERMINAL_STATUSES",
    "AnonymousWriteCommandError",
    "ClaimOutcome",
    "ClaimResult",
    "CommandReason",
    "CommandStatus",
    "IdempotencyConflictError",
    "IdempotencyKey",
    "InvalidCommandParametersError",
    "InvalidIdempotencyKeyError",
    "ReadActionNotAllowedError",
    "UnknownWriteActionError",
    "WriteCommandClaim",
    "WriteCommandCoordinator",
    "WriteCommandError",
    "WriteCommandOutcome",
    "WriteCommandRecord",
    "WriteCommandResult",
    "WriteCommandStore",
    "WriteCommandStoreError",
    "canonical_json",
    "hash_idempotency_key",
    "request_fingerprint",
    "snapshot_parameters",
]
