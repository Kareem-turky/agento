"""``NativeCommerceAdapter``: the commerce integration contract over the Product's own
canonical commerce store.

    CommerceIntegration call -> CommerceStoreReader (PostgresCommerceStore) -> canonical models

The store already returns validated canonical models and applies the contract's query
semantics (filters, ordering and limit in SQL). This adapter only translates the typed
queries into the store's primitive filters and the store's errors into the existing
Product integration errors:

    CommerceStoreNotFoundError    -> IntegrationNotFoundError (same entity and id)
    CommerceStoreDataError        -> IntegrationDataError (fixed reason)
    CommerceStoreUnavailableError -> IntegrationUnavailableError("native-commerce")

Nothing from the store's internals crosses this boundary (errors are not chained).
Stored commerce records are data, never authorization.
"""

from collections.abc import Awaitable
from uuid import UUID

from app.commerce.domain import InventoryLevel, Order, Shipment, Store
from app.commerce.store import (
    CommerceStoreDataError,
    CommerceStoreError,
    CommerceStoreNotFoundError,
    CommerceStoreReader,
)
from app.integrations.commerce.capabilities import IntegrationCapability, IntegrationDescriptor
from app.integrations.commerce.errors import (
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
)
from app.integrations.commerce.queries import OrderQuery, ShipmentQuery

NATIVE_DESCRIPTOR = IntegrationDescriptor(
    id="native-commerce",
    name="Native Commerce Store",
    capabilities=frozenset(
        {
            IntegrationCapability.ORDERS_READ,
            IntegrationCapability.SHIPMENTS_READ,
            IntegrationCapability.INVENTORY_READ,
        }
    ),  # fmt: skip
)
_DATA_REASON = "stored canonical data is invalid"


async def _translated[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except CommerceStoreNotFoundError as error:
        raise IntegrationNotFoundError(error.entity, error.entity_id) from None
    except CommerceStoreDataError as error:
        raise IntegrationDataError(error.entity, _DATA_REASON) from None
    except CommerceStoreError:
        # Unavailable, or anything else the store could not answer: fail closed.
        raise IntegrationUnavailableError(NATIVE_DESCRIPTOR.id) from None


class NativeCommerceAdapter:
    """Implements ``CommerceIntegration`` over a ``CommerceStoreReader``."""

    def __init__(self, store: CommerceStoreReader) -> None:
        self._store = store

    @property
    def descriptor(self) -> IntegrationDescriptor:
        return NATIVE_DESCRIPTOR

    async def get_store(self, store_id: UUID) -> Store:
        return await _translated(self._store.get_store(store_id))

    async def get_order(self, order_id: UUID) -> Order:
        return await _translated(self._store.get_order(order_id))

    async def list_orders(self, query: OrderQuery | None = None) -> tuple[Order, ...]:
        query = query or OrderQuery()
        return await _translated(self._store.list_orders(
            store_id=query.store_id, statuses=query.statuses, created_from=query.created_from,
            created_to=query.created_to, limit=query.limit,
        ))  # fmt: skip

    async def get_shipment(self, shipment_id: UUID) -> Shipment:
        return await _translated(self._store.get_shipment(shipment_id))

    async def list_shipments(self, query: ShipmentQuery | None = None) -> tuple[Shipment, ...]:
        query = query or ShipmentQuery()
        return await _translated(self._store.list_shipments(
            order_id=query.order_id, store_id=query.store_id, statuses=query.statuses,
            shipped_from=query.shipped_from, shipped_to=query.shipped_to, limit=query.limit,
        ))  # fmt: skip

    async def get_inventory(
        self, variant_id: UUID, warehouse_id: UUID | None = None
    ) -> tuple[InventoryLevel, ...]:
        return await _translated(self._store.get_inventory(variant_id, warehouse_id))
