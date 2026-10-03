"""Human-approval lifecycle vocabularies and the explicit state machine (Task 036).

    requested ──> approved | rejected | expired | cancelled

Every other transition is refused. All four outcomes are TERMINAL: a rejected, expired or
cancelled request never becomes approved, an approved one never becomes rejected, and a
new business attempt needs a new request. These states describe the HUMAN decision only:
what happened when a granted request was executed is separate consumption metadata
(``consumed_at``, ``execution_outcome``), never a status.
"""

from enum import StrEnum


class ApprovalStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalEventType(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    EXECUTION_CLAIMED = "execution_claimed"
    EXECUTION_COMPLETED = "execution_completed"


class InvalidApprovalTransitionError(ValueError):
    """A change outside the explicit state machine (never applied)."""


TERMINAL_STATUSES = frozenset(
    {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED,
     ApprovalStatus.CANCELLED}
)  # fmt: skip
HUMAN_DECISIONS = frozenset(
    {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED}
)
_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.REQUESTED: TERMINAL_STATUSES,
    **{status: frozenset() for status in TERMINAL_STATUSES},
}
EVENT_FOR_STATUS: dict[ApprovalStatus, ApprovalEventType] = {
    ApprovalStatus.APPROVED: ApprovalEventType.APPROVED,
    ApprovalStatus.REJECTED: ApprovalEventType.REJECTED,
    ApprovalStatus.EXPIRED: ApprovalEventType.EXPIRED,
    ApprovalStatus.CANCELLED: ApprovalEventType.CANCELLED,
}


def check_transition(current: ApprovalStatus, new: ApprovalStatus) -> None:
    if new not in _TRANSITIONS[ApprovalStatus(current)]:
        raise InvalidApprovalTransitionError("invalid approval transition")
