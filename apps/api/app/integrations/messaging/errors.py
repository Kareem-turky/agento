"""Messaging integration errors: fixed messages only (never a provider payload, a
message text, an external reference, a URL or a credential)."""


class MessagingIntegrationError(Exception):
    message = "messaging integration error"

    def __init__(self) -> None:
        super().__init__(self.message)


class MessagingUnavailableError(MessagingIntegrationError):
    """The provider could not be reached and NOTHING was sent (safe to report)."""

    message = "messaging provider unavailable"


class MessagingSendRejectedError(MessagingIntegrationError):
    """The provider definitively refused the message: nothing was sent."""

    message = "messaging provider rejected the message"


class MessagingSendUncertainError(MessagingIntegrationError):
    """The outcome is unknown (timeout after the request left, malformed answer...).
    The message MAY have been sent: it must never be blindly retried."""

    message = "messaging send outcome unknown"


class MessagingRegistryError(ValueError):
    """The static messaging registry is inconsistent with the Integration Catalog."""
