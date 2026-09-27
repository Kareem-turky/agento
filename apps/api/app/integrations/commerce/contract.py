"""The product-owned commerce integration contract.

Application code, and later tools and agents, depend on ``CommerceIntegration``,
never on a provider API. Every adapter (mock today; real systems later) implements
it and returns canonical domain models only.

The contract is async because real integrations do network or database I/O. It is
read-only and deliberately knows nothing about actors, permissions, policy or
frameworks: authorization will wrap these calls in a later layer.

Errors: unknown canonical IDs raise ``IntegrationNotFoundError``; an unreachable
system raises ``IntegrationUnavailableError``; unmappable provider data raises
``IntegrationDataError``.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.commerce.domain import InventoryLevel, Order, Shipment, Store
from app.integrations.commerce.capabilities import IntegrationDescriptor
from app.integrations.commerce.queries import OrderQuery, ShipmentQuery


@runtime_checkable
class CommerceIntegration(Protocol):
    @property
    def descriptor(self) -> IntegrationDescriptor:
        """What this integration is and which read capabilities it provides."""
        ...

    async def get_store(self, store_id: UUID) -> Store: ...

    async def get_order(self, order_id: UUID) -> Order: ...

    async def list_orders(self, query: OrderQuery | None = None) -> tuple[Order, ...]: ...

    async def get_shipment(self, shipment_id: UUID) -> Shipment: ...

    async def list_shipments(self, query: ShipmentQuery | None = None) -> tuple[Shipment, ...]: ...

    async def get_inventory(
        self, variant_id: UUID, warehouse_id: UUID | None = None
    ) -> tuple[InventoryLevel, ...]:
        """All levels for a known variant (optionally one warehouse).

        Unknown variant or warehouse -> ``IntegrationNotFoundError``; a known variant
        with no stock records -> empty tuple. Sorted by ``warehouse_id``.
        """
        ...
