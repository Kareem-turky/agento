"""The Product-owned canonical commerce store contract (Task 031).

The Product owns its canonical commerce data. ``CommerceStoreReader`` is the narrow
read port over that state; ``app.persistence.PostgresCommerceStore`` implements it and
``app.integrations.commerce.native.NativeCommerceAdapter`` consumes it, so neither
layer depends on the other:

    NativeCommerceAdapter -> CommerceStoreReader <- PostgresCommerceStore

Everything here is canonical: domain models and primitive filters only (no SQL, no
query objects of the integration contract, no provider data). Errors carry only an
entity type, a canonical id or a fixed message; never SQL, database URLs,
credentials, driver exceptions or stored values.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.commerce.domain import (
    InventoryLevel,
    Order,
    OrderStatus,
    Shipment,
    ShipmentStatus,
    Store,
)


class CommerceStoreError(Exception):
    """Base class for every error raised by a canonical commerce store."""


class CommerceStoreNotFoundError(CommerceStoreError):
    """The canonical entity does not exist in the store."""

    def __init__(self, entity: str, entity_id: UUID) -> None:
        super().__init__(f"{entity} {entity_id} was not found")
        self.entity = entity
        self.entity_id = entity_id


class CommerceStoreDataError(CommerceStoreError):
    """Stored state cannot be reconstructed as a valid canonical model (fail closed:
    nothing is coerced, guessed or partially returned)."""

    def __init__(self, entity: str) -> None:
        super().__init__(f"stored {entity} data is not valid canonical data")
        self.entity = entity


class CommerceStoreUnavailableError(CommerceStoreError):
    """The store could not be reached or did not answer."""

    def __init__(self) -> None:
        super().__init__("the commerce store is unavailable")


class CommerceStoreWriteError(CommerceStoreError):
    """A canonical write was rejected (for example an unknown related entity) and
    nothing of it was committed."""

    def __init__(self, entity: str, entity_id: UUID) -> None:
        super().__init__(f"{entity} {entity_id} could not be stored")
        self.entity = entity
        self.entity_id = entity_id


@runtime_checkable
class CommerceStoreReader(Protocol):
    """Read port over the Product's canonical commerce state.

    Semantics are those of the commerce integration contract: empty ``statuses`` means
    any status, time ranges are half-open (``from <= value < to``), filters combine
    with AND and ``limit`` applies after filtering and sorting. Orders sort by
    ``created_at`` then ``id``; shipments by ``shipped_at`` (unshipped last) then
    ``id``; a shipment's store is its PARENT order's store; inventory sorts by
    ``warehouse_id``. Unknown ids in a ``get_*`` (and an unknown variant/warehouse
    for inventory) raise ``CommerceStoreNotFoundError``; unknown list scopes are empty.
    """

    async def get_store(self, store_id: UUID) -> Store: ...

    async def get_order(self, order_id: UUID) -> Order: ...

    async def list_orders(
        self,
        *,
        store_id: UUID | None,
        statuses: frozenset[OrderStatus],
        created_from: datetime | None,
        created_to: datetime | None,
        limit: int | None,
    ) -> tuple[Order, ...]: ...

    async def get_shipment(self, shipment_id: UUID) -> Shipment: ...

    async def list_shipments(
        self,
        *,
        order_id: UUID | None,
        store_id: UUID | None,
        statuses: frozenset[ShipmentStatus],
        shipped_from: datetime | None,
        shipped_to: datetime | None,
        limit: int | None,
    ) -> tuple[Shipment, ...]: ...

    async def get_inventory(
        self, variant_id: UUID, warehouse_id: UUID | None
    ) -> tuple[InventoryLevel, ...]: ...
