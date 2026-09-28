"""Commerce integration contracts (read-only commerce, ticketing), capabilities,
queries and errors."""

from app.integrations.commerce.capabilities import (
    IntegrationCapability,
    IntegrationDescriptor,
)
from app.integrations.commerce.contract import CommerceIntegration
from app.integrations.commerce.errors import (
    CommerceIntegrationError,
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
    IntegrationWriteError,
    IntegrationWriteRejectedError,
    IntegrationWriteUncertainError,
)
from app.integrations.commerce.queries import MAX_QUERY_LIMIT, OrderQuery, ShipmentQuery
from app.integrations.commerce.ticketing import TicketingIntegration

__all__ = [
    "MAX_QUERY_LIMIT",
    "CommerceIntegration",
    "CommerceIntegrationError",
    "IntegrationCapability",
    "IntegrationDataError",
    "IntegrationDescriptor",
    "IntegrationNotFoundError",
    "IntegrationUnavailableError",
    "IntegrationWriteError",
    "IntegrationWriteRejectedError",
    "IntegrationWriteUncertainError",
    "OrderQuery",
    "ShipmentQuery",
    "TicketingIntegration",
]
