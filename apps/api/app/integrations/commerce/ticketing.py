"""The product-owned ticketing integration contract (operational tickets).

The first WRITE contract. Application code depends on ``TicketingIntegration``,
never on a provider API; adapters return canonical ``Ticket`` models only.

``correlation_id`` is a trusted per-execution key chosen by the caller (the governed
action uses its run id). The external system records it with the ticket so that a
later, independent read can find the ticket even when the create call returned no
trustworthy answer. It is not a complete cross-run retry or idempotency system.

Errors:
- ``create_ticket``: ``IntegrationWriteRejectedError`` when nothing was written
  (unknown or mismatched company/store, provider rejection, provider unreachable
  before sending); ``IntegrationWriteUncertainError`` when the write may have happened.
- ``get_ticket``: ``IntegrationNotFoundError`` for an unknown ticket;
  ``IntegrationUnavailableError`` when the system cannot be read.
- ``find_ticket_by_correlation``: ``None`` when no ticket carries that correlation.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.commerce.domain import Ticket


@runtime_checkable
class TicketingIntegration(Protocol):
    async def create_ticket(
        self,
        *,
        company_id: UUID,
        store_id: UUID,
        title: str,
        description: str,
        correlation_id: UUID,
    ) -> Ticket: ...

    async def get_ticket(self, ticket_id: UUID) -> Ticket: ...

    async def find_ticket_by_correlation(self, correlation_id: UUID) -> Ticket | None: ...
