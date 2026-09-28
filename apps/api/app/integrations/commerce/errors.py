"""Product-level integration errors.

Adapters translate provider/transport exceptions into these types (chaining the
original with ``raise ... from exc``). They carry no HTTP status codes and no
provider SDK types; mapping them to API responses is a later layer's job.
"""

from uuid import UUID


class CommerceIntegrationError(Exception):
    """Base class for every error raised through the commerce integration contract."""


class IntegrationNotFoundError(CommerceIntegrationError):
    """The requested canonical entity does not exist in the external system."""

    def __init__(self, entity: str, entity_id: UUID) -> None:
        super().__init__(f"{entity} {entity_id} was not found")
        self.entity = entity
        self.entity_id = entity_id


class IntegrationUnavailableError(CommerceIntegrationError):
    """The external system could not be reached or failed to respond."""

    def __init__(self, integration_id: str) -> None:
        super().__init__(f"integration {integration_id!r} is unavailable")
        self.integration_id = integration_id


class IntegrationDataError(CommerceIntegrationError):
    """The external system returned data that cannot be safely mapped to the
    canonical domain. Nothing is coerced or guessed."""

    def __init__(self, entity: str, reason: str) -> None:
        super().__init__(f"cannot map {entity} to the canonical model: {reason}")
        self.entity = entity
        self.reason = reason


class IntegrationWriteError(CommerceIntegrationError):
    """A write through the integration did not complete with a trustworthy result.

    ``effect_may_have_occurred`` tells the caller whether the external system may
    have applied the write anyway. Provider details are never part of the message.
    """

    effect_may_have_occurred: bool = True

    def __init__(self, entity: str) -> None:
        super().__init__(f"{entity} write did not complete")
        self.entity = entity


class IntegrationWriteRejectedError(IntegrationWriteError):
    """The write definitely did NOT happen (rejected or failed before sending)."""

    effect_may_have_occurred = False


class IntegrationWriteUncertainError(IntegrationWriteError):
    """The write MAY have happened (e.g. timeout or disconnect after sending)."""

    effect_may_have_occurred = True
