"""Employee Chat errors: fixed, value-free messages (never a submitted value)."""


class EmployeeChatError(Exception):
    message = "Employee chat error"

    def __init__(self) -> None:
        super().__init__(self.message)


class ChatForbiddenError(EmployeeChatError):
    """The selected store is not granted to the actor (nothing was created or run)."""

    message = "Forbidden"


class ChatNotFoundError(EmployeeChatError):
    """Unknown, another actor's, another company's, or a store no longer granted:
    deliberately indistinguishable."""

    message = "Chat thread not found"


class ChatProposalNotFoundError(EmployeeChatError):
    message = "Ticket proposal not found"


class ChatTurnConflictError(EmployeeChatError):
    """The turn id was already used for a different message (or thread)."""

    message = "Chat turn conflict"


class ChatTurnInProgressError(EmployeeChatError):
    message = "Chat turn in progress"


class ChatAgentDisabledError(EmployeeChatError):
    message = "Operations Agent is disabled"


class ChatProposalCancelledError(EmployeeChatError):
    message = "Ticket proposal was cancelled"


class ChatProposalAlreadyConfirmedError(EmployeeChatError):
    """Confirmed already (with another Idempotency-Key), or no longer cancellable."""

    message = "Ticket proposal was already confirmed"


class ChatInvalidIdempotencyKeyError(EmployeeChatError):
    message = "Invalid Idempotency-Key"


class ChatIdempotencyConflictError(EmployeeChatError):
    message = "Idempotency conflict"


class ChatUnavailableError(EmployeeChatError):
    message = "Employee chat unavailable"


class ChatRepositoryError(Exception):
    """Persistence failed. Fixed message; never driver or SQL detail."""

    def __init__(self) -> None:
        super().__init__("employee chat repository error")
