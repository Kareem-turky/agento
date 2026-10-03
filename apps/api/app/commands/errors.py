"""Typed write-command errors. Messages are fixed codes: never raw keys, parameters,
fingerprints or underlying exception text."""


class WriteCommandError(Exception):
    """Base class. ``code`` is a stable, safe identifier (e.g. for a future HTTP mapping)."""

    code = "write_command_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class AnonymousWriteCommandError(WriteCommandError):
    """The request has no actor. Rejected before any claim: no anonymous namespace."""

    code = "actor_required"


class InvalidIdempotencyKeyError(WriteCommandError):
    code = "idempotency_key_invalid"


class UnknownWriteActionError(WriteCommandError):
    """The action is not in the trusted catalog. Rejected before any claim."""

    code = "action_unknown"


class ReadActionNotAllowedError(WriteCommandError):
    """Read actions never go through the durable write-command path."""

    code = "read_action_not_allowed"


class InvalidCommandParametersError(WriteCommandError):
    """The parameters are not plain JSON, so no deterministic fingerprint exists."""

    code = "parameters_not_canonical"


class IdempotencyConflictError(WriteCommandError):
    """The idempotency key was already used for a different request. Nothing ran."""

    code = "idempotency_conflict"


class WriteCommandStoreError(WriteCommandError):
    """The command store failed or returned data that cannot be trusted.

    Raised by a claim failure (nothing executed) or by corrupt/unknown stored state
    (fail closed: never read as success).
    """

    code = "command_store_unavailable"


class ApprovalContinuationRefusedError(WriteCommandError):
    """Task 036: an approval-linked command is not available to this caller: the caller is
    not the exact requester principal (actor id AND actor type) of the approval the
    command is linked to, or the approval id presented to continue it is not the one it
    awaits. Nothing ran, nothing changed and nothing about the command is returned."""

    code = "approval_continuation_refused"
