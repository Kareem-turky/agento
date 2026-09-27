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
