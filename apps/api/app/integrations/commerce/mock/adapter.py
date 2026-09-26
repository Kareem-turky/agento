"""Mock commerce adapter: provider records in, canonical domain models out.

This is the trust boundary. Every provider record is validated into canonical
Pydantic models; anything that cannot be represented safely raises
``IntegrationDataError`` (the original error is chained, never leaked as the
contract-level type). Provider failures become ``IntegrationUnavailableError``.

Canonical UUIDs are resolved back to provider keys by scanning the provider's keys
and recomputing the deterministic UUID5 (``identity.canonical_id``); provider IDs are
never accepted through the contract and appear only in ``ExternalReference``.
"""

from collections.abc import Callable, Iterable
from datetime import datetime
from uuid import UUID

from app.commerce.domain import (
    InventoryLevel,
    Order,
    OrderItem,
    Shipment,
    Store,
)
from app.integrations.commerce.capabilities import IntegrationCapability, IntegrationDescriptor
from app.integrations.commerce.errors import (
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
)
from app.integrations.commerce.mock.identity import MOCK_SYSTEM_ID, EntityType, canonical_id
from app.integrations.commerce.mock.mapping import map_delivery_state, map_order_state
from app.integrations.commerce.mock.models import (
    MockOrderRecord,
    MockShipmentRecord,
    MockShopRecord,
    MockStockRecord,
)
from app.integrations.commerce.mock.system import MockCommerceSystem, MockProviderDownError
from app.integrations.commerce.queries import OrderQuery, ShipmentQuery

MOCK_DESCRIPTOR = IntegrationDescriptor(
    id=MOCK_SYSTEM_ID,
    name="Mock Commerce System",
    capabilities=frozenset(IntegrationCapability),
)


def _refs(external_id: str) -> list[dict[str, str]]:
    return [{"system": MOCK_SYSTEM_ID, "external_id": external_id}]


def _text(value: object) -> str:
    """Provider amounts and quantities are strings; anything else is corrupt data."""
    if not isinstance(value, str):
        raise TypeError("expected a string value from the provider")
    return value


def _timestamp(value: object) -> datetime | None:
    """Provider timestamps are ISO-8601 strings; numbers are not reinterpreted as epochs.
    Naive results are rejected by the canonical model."""
    if value is None:
        return None
    return datetime.fromisoformat(_text(value))


def _in_range(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if start is None and end is None:
        return True
    if value is None:
        return False
    return (start is None or start <= value) and (end is None or value < end)


class MockCommerceAdapter:
    """Implements ``CommerceIntegration`` on top of ``MockCommerceSystem``."""

    def __init__(self, system: MockCommerceSystem | None = None) -> None:
        self._system = system if system is not None else MockCommerceSystem()

    @property
    def descriptor(self) -> IntegrationDescriptor:
        return MOCK_DESCRIPTOR

    # ----- contract -------------------------------------------------------------

    async def get_store(self, store_id: UUID) -> Store:
        return self._call(lambda: self._map_store(self._require_shop(store_id)))

    async def get_order(self, order_id: UUID) -> Order:
        def read() -> Order:
            key = self._resolve(EntityType.ORDER, order_id, self._order_keys())
            record = self._system.fetch_order(key)
            if record is None:
                raise IntegrationNotFoundError("order", order_id)
            return self._map_order(record)

        return self._call(read)

    async def list_orders(self, query: OrderQuery | None = None) -> tuple[Order, ...]:
        query = query or OrderQuery()

        def read() -> tuple[Order, ...]:
            shop_key = None
            if query.store_id is not None:
                shop_key = self._resolve_optional(
                    EntityType.STORE, query.store_id, self._system.list_shop_keys()
                )
                if shop_key is None:
                    return ()  # an unknown store simply has no orders
            orders = [self._map_order(r) for r in self._system.search_orders(shop_key)]
            matching = [
                o
                for o in orders
                if (not query.statuses or o.status in query.statuses)
                and _in_range(o.created_at, query.created_from, query.created_to)
            ]
            matching.sort(key=lambda o: (o.created_at, o.id))
            return tuple(matching[: query.limit])

        return self._call(read)

    async def get_shipment(self, shipment_id: UUID) -> Shipment:
        def read() -> Shipment:
            key = self._resolve(EntityType.SHIPMENT, shipment_id, self._shipment_keys())
            record = self._system.fetch_shipment(key)
            if record is None:
                raise IntegrationNotFoundError("shipment", shipment_id)
            return self._map_shipment(record)

        return self._call(read)

    async def list_shipments(self, query: ShipmentQuery | None = None) -> tuple[Shipment, ...]:
        query = query or ShipmentQuery()

        def read() -> tuple[Shipment, ...]:
            order_key = None
            if query.order_id is not None:
                order_key = self._resolve_optional(
                    EntityType.ORDER, query.order_id, self._order_keys()
                )
                if order_key is None:
                    return ()  # an unknown order simply has no shipments
            shipments = [self._map_shipment(r) for r in self._system.search_shipments(order_key)]
            matching = [
                s
                for s in shipments
                if (not query.statuses or s.status in query.statuses)
                and _in_range(s.shipped_at, query.shipped_from, query.shipped_to)
            ]
            matching.sort(key=lambda s: (s.shipped_at is None, s.shipped_at or datetime.min, s.id))
            return tuple(matching[: query.limit])

        return self._call(read)

    async def get_inventory(
        self, variant_id: UUID, warehouse_id: UUID | None = None
    ) -> tuple[InventoryLevel, ...]:
        def read() -> tuple[InventoryLevel, ...]:
            sku_key = self._resolve(EntityType.VARIANT, variant_id, self._system.list_sku_keys())
            location_key = None
            if warehouse_id is not None:
                location_key = self._resolve(
                    EntityType.WAREHOUSE, warehouse_id, self._system.list_location_keys()
                )
            levels = [self._map_stock(r) for r in self._system.fetch_stock(sku_key, location_key)]
            return tuple(sorted(levels, key=lambda level: level.warehouse_id))

        return self._call(read)

    # ----- error translation and identity resolution ------------------------------

    def _call[T](self, read: Callable[[], T]) -> T:
        try:
            return read()
        except MockProviderDownError as exc:
            raise IntegrationUnavailableError(MOCK_SYSTEM_ID) from exc

    @staticmethod
    def _resolve_optional(entity: EntityType, wanted: UUID, keys: Iterable[str]) -> str | None:
        return next((k for k in keys if canonical_id(entity, k) == wanted), None)

    def _resolve(self, entity: EntityType, wanted: UUID, keys: Iterable[str]) -> str:
        key = self._resolve_optional(entity, wanted, keys)
        if key is None:
            raise IntegrationNotFoundError(entity.value, wanted)
        return key

    def _order_keys(self) -> tuple[str, ...]:
        return tuple(o.order_key for o in self._system.search_orders())

    def _shipment_keys(self) -> tuple[str, ...]:
        return tuple(s.shipment_key for s in self._system.search_shipments())

    def _require_shop(self, store_id: UUID) -> MockShopRecord:
        key = self._resolve(EntityType.STORE, store_id, self._system.list_shop_keys())
        shop = self._system.fetch_shop(key)
        if shop is None:
            raise IntegrationNotFoundError("store", store_id)
        return shop

    # ----- mapping (the trust boundary) -------------------------------------------

    @staticmethod
    def _mapped[T](entity: str, build: Callable[[], T]) -> T:
        try:
            return build()
        except IntegrationDataError:
            raise
        except (ValueError, TypeError, AttributeError) as exc:
            # pydantic.ValidationError is a ValueError. Provider IDs are not echoed.
            raise IntegrationDataError(entity, "provider record failed validation") from exc

    def _known[R](self, entity: str, found: R | None) -> R:
        if found is None:
            raise IntegrationDataError(entity, "references a record the provider does not have")
        return found

    # Relationship integrity: every provider reference must exist and belong to the same
    # account/shop as the record that points at it; otherwise it is a data error.

    def _owned_by_account(self, entity: str, account_key: str) -> None:
        if account_key != self._system.fetch_account().account_key:
            raise IntegrationDataError(entity, "belongs to an unknown account")

    def _checked_shop(self, entity: str, shop_key: str) -> MockShopRecord:
        shop = self._known(entity, self._system.fetch_shop(shop_key))
        self._owned_by_account(entity, shop.account_key)
        return shop

    def _checked_buyer(self, entity: str, buyer_key: str, shop_key: str) -> None:
        buyer = self._known(entity, self._system.fetch_buyer(buyer_key))
        if buyer.shop_key != shop_key:
            raise IntegrationDataError(entity, "references a customer of another store")

    def _checked_sku(self, entity: str, sku_key: str, shop_key: str | None = None) -> None:
        """SKU -> listing must exist; with ``shop_key``, the listing must be that shop's.
        Without it (inventory), the listing's shop must belong to this account."""
        sku = self._known(entity, self._system.fetch_sku(sku_key))
        listing = self._known(entity, self._system.fetch_listing(sku.listing_key))
        if shop_key is None:
            self._checked_shop(entity, listing.shop_key)
        elif listing.shop_key != shop_key:
            raise IntegrationDataError(entity, "references a product of another store")

    def _checked_location(self, entity: str, location_key: str) -> None:
        location = self._known(entity, self._system.fetch_location(location_key))
        self._owned_by_account(entity, location.account_key)

    def _map_store(self, shop: MockShopRecord) -> Store:
        def build() -> Store:
            self._owned_by_account("store", shop.account_key)
            return Store.model_validate(
                {
                    "id": canonical_id(EntityType.STORE, shop.shop_key),
                    "company_id": canonical_id(EntityType.COMPANY, shop.account_key),
                    "name": shop.label,
                    "currency": shop.currency_code,
                    "timezone": shop.tz_name,
                    "external_refs": _refs(shop.shop_key),
                }
            )

        return self._mapped("store", build)

    def _map_order(self, record: MockOrderRecord) -> Order:
        def build() -> Order:
            self._checked_shop("order", record.shop_key)
            customer_id = None
            if record.buyer_key is not None:
                self._checked_buyer("order", record.buyer_key, record.shop_key)
                customer_id = canonical_id(EntityType.CUSTOMER, record.buyer_key)
            items = []
            for line in record.lines:
                variant_id = None
                if line.sku_key is not None:
                    self._checked_sku("order item", line.sku_key, record.shop_key)
                    variant_id = canonical_id(EntityType.VARIANT, line.sku_key)
                items.append(
                    OrderItem.model_validate(
                        {
                            "id": canonical_id(EntityType.ORDER_ITEM, line.line_key),
                            "variant_id": variant_id,
                            "sku": line.sku_key,
                            "title": line.description,
                            "quantity": _text(line.qty),
                            "unit_price": {
                                "amount": _text(line.unit_amount),
                                "currency": record.currency,
                            },
                            "external_refs": _refs(line.line_key),
                        }
                    )
                )
            return Order.model_validate(
                {
                    "id": canonical_id(EntityType.ORDER, record.order_key),
                    "store_id": canonical_id(EntityType.STORE, record.shop_key),
                    "customer_id": customer_id,
                    "status": map_order_state(record.state),
                    "source_status": record.state,
                    "items": tuple(items),
                    "total": {"amount": _text(record.gross_amount), "currency": record.currency},
                    "created_at": _timestamp(record.created_timestamp),
                    "updated_at": _timestamp(record.modified_timestamp),
                    "external_refs": _refs(record.order_key),
                }
            )

        return self._mapped("order", build)

    def _map_shipment(self, record: MockShipmentRecord) -> Shipment:
        def build() -> Shipment:
            self._known("shipment", self._system.fetch_order(record.order_key))
            return Shipment.model_validate(
                {
                    "id": canonical_id(EntityType.SHIPMENT, record.shipment_key),
                    "order_id": canonical_id(EntityType.ORDER, record.order_key),
                    "status": map_delivery_state(record.delivery_state),
                    "source_status": record.delivery_state,
                    "courier_name": record.carrier,
                    "tracking_number": record.tracking_code,
                    "shipped_at": _timestamp(record.dispatched_timestamp),
                    "delivered_at": _timestamp(record.received_timestamp),
                    "external_refs": _refs(record.shipment_key),
                }
            )

        return self._mapped("shipment", build)

    def _map_stock(self, record: MockStockRecord) -> InventoryLevel:
        def build() -> InventoryLevel:
            self._checked_location("inventory level", record.location_key)
            self._checked_sku("inventory level", record.sku_key)
            return InventoryLevel.model_validate(
                {
                    "id": canonical_id(
                        EntityType.INVENTORY_LEVEL, f"{record.sku_key}@{record.location_key}"
                    ),
                    "variant_id": canonical_id(EntityType.VARIANT, record.sku_key),
                    "warehouse_id": canonical_id(EntityType.WAREHOUSE, record.location_key),
                    "available": _text(record.sellable),
                    "on_hand": None if record.physical is None else _text(record.physical),
                    "reserved": None if record.held is None else _text(record.held),
                    "updated_at": _timestamp(record.counted_timestamp),
                }
            )

        return self._mapped("inventory level", build)
