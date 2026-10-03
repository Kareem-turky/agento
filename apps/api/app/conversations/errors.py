"""Conversation errors: fixed, value-free messages (never message text, an external
reference, a provider payload, SQL or a credential)."""

from enum import StrEnum


class ConversationError(Exception):
    message = "conversation operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class ConversationAccessDeniedError(ConversationError):
    message = "Forbidden"


class ConversationNotFoundError(ConversationError):
    """Unknown, another company's, or outside the actor's stores: indistinguishable."""

    message = "Conversation not found"


class ConversationInputReason(StrEnum):
    FILTER_INVALID = "filter_invalid"


class ConversationInputError(ConversationError):
    message = "Invalid conversation request"

    def __init__(self, reason: ConversationInputReason) -> None:
        super().__init__()
        self.reason = ConversationInputReason(reason)


class ConversationUnavailableError(ConversationError):
    message = "Conversations unavailable"


class IngressRefusal(StrEnum):
    """Why an inbound message was not accepted (stable, provider-independent codes)."""

    CONNECTION_NOT_FOUND = "connection_not_found"
    CONNECTION_DISABLED = "connection_disabled"
    INTEGRATION_NOT_INSTALLED = "integration_not_installed"
    NOT_MESSAGING = "not_messaging"
    RECEIVE_NOT_SUPPORTED = "receive_not_supported"
    ENVELOPE_INVALID = "envelope_invalid"
    STORE_CONFLICT = "store_conflict"


class InboundMessageRefusedError(ConversationError):
    """The channel or the envelope is not acceptable: nothing was recorded."""

    message = "Inbound message refused"

    def __init__(self, reason: IngressRefusal) -> None:
        super().__init__()
        self.reason = IngressRefusal(reason)


class InboundMessageConflictError(ConversationError):
    """The external message reference was already recorded with DIFFERENT canonical
    content: the original is kept and nothing new is recorded."""

    message = "Inbound message conflicts with a recorded message"


class DeliveryRefusal(StrEnum):
    MESSAGE_NOT_FOUND = "message_not_found"
    NOT_OUTBOUND = "not_outbound"
    INVALID_STATE = "invalid_state"


class DeliveryUpdateRefusedError(ConversationError):
    message = "Delivery update refused"

    def __init__(self, reason: DeliveryRefusal) -> None:
        super().__init__()
        self.reason = DeliveryRefusal(reason)


class DeliveryEventConflictError(ConversationError):
    """The external delivery event reference was already recorded with different data."""

    message = "Delivery event conflicts with a recorded event"


class ConversationRepositoryError(Exception):
    """Conversation storage could not answer or holds invalid data (fixed message)."""

    def __init__(self) -> None:
        super().__init__("conversation storage unavailable")


class ConversationStoreConflictError(Exception):
    """Repository signal: the trusted store differs from the conversation's store."""


class MessageFingerprintConflictError(Exception):
    """Repository signal: same external message ref, different canonical content."""


class DeliveryEventFingerprintConflictError(Exception):
    """Repository signal: same external delivery event ref, different data."""
