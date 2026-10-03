"""Approval errors: fixed messages and stable codes, never a submitted or stored value."""

from enum import StrEnum


class ApprovalInputReason(StrEnum):
    NOTE_REQUIRED = "note_required"
    NOTE_INVALID = "note_invalid"
    FILTER_INVALID = "filter_invalid"


class ApprovalError(Exception):
    message = "approval operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class ApprovalAccessDeniedError(ApprovalError):
    message = "Forbidden"


class ApprovalSelfDecisionError(ApprovalAccessDeniedError):
    """Two-person rule: a requester never decides their own request."""

    message = "Forbidden"


class ApprovalNotFoundError(ApprovalError):
    message = "Approval not found"


class ApprovalConflictError(ApprovalError):
    """The request is no longer awaiting a decision (or the change lost a race)."""

    message = "Approval is no longer pending"


class ApprovalNotResumableError(ApprovalError):
    message = "Approval cannot resume a workflow"


class ApprovalInputError(ApprovalError):
    message = "Invalid approval input"

    def __init__(self, reason: ApprovalInputReason) -> None:
        super().__init__()
        self.reason = ApprovalInputReason(reason)


class ApprovalOperationFailedError(ApprovalError):
    message = "Approval operation did not complete"


class ApprovalUnavailableError(ApprovalError):
    message = "Approvals unavailable"


class ApprovalRepositoryError(Exception):
    """Durable approval state could not be read or written, or is malformed (fixed
    message, never chained)."""

    def __init__(self) -> None:
        super().__init__("approval state unavailable")


class ApprovalTransitionConflictError(Exception):
    """A compare-and-set transition found the request no longer in the expected state
    (nothing written)."""
