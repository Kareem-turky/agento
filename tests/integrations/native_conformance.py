"""NativeCommerceAdapter's CommerceConformanceFixture (native-specific glue only).

The data is built directly from canonical Product models with its OWN identities (a
test-only UUID5 namespace): no mock system, records, provider ids or mock identity
function. It is written through ``PostgresCommerceStore`` into the real, migrated
PostgreSQL and read back through ``NativeCommerceAdapter``:

- unavailable: a deterministic seam (an engine whose connection attempt fails at once,
  its error carrying planted secrets), so no network timeout is involved;
- corrupted: valid canonical rows are seeded, then ONE row is damaged with test-only
  direct SQL (the damage carries planted markers).

Every adapter uses a NullPool engine: the generic suite runs each read in its own event
loop, and no pooled connection may cross loops.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.commerce.domain import (
    Company,
    ExternalReference,
    InventoryLevel,
    Money,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductStatus,
    Shipment,
    ShipmentStatus,
    Store,
    Variant,
    Warehouse,
)
from app.integrations.commerce import CommerceIntegration
from app.integrations.commerce.native import NativeCommerceAdapter
from app.persistence import (
    COMMERCE_TABLES,
    PostgresCommerceStore,
    create_product_engine,
    create_session_factory,
)
from tests.commerce_conformance import CommerceConformanceFixture

NAMESPACE = UUID("6f0c8f52-3b7e-4f3a-9d0e-0a5b7c1d2e30")  # test-only, native fixture


def cid(name: str) -> UUID:
    return uuid5(NAMESPACE, name)


# Planted INSIDE the store (damaged rows) or the failing connection; none may appear in
# a Product-level error.
CREDENTIAL_MARKER = "NATIVE-CONFORMANCE-DB-PASSWORD-7c2a"
URL_MARKER = "postgresql://native:NATIVE-CONFORMANCE-DB-PASSWORD-7c2a@db.invalid/commerce"
RAW_MARKER = "NATIVE-CONFORMANCE-RAW-ROW-MARKER"
MARKERS = frozenset({CREDENTIAL_MARKER, URL_MARKER, RAW_MARKER, "db.invalid"})

T0 = datetime(2031, 5, 4, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class NativeDataset:
    company: Company
    stores: tuple[Store, ...]
    products: tuple[Product, ...]
    variants: tuple[Variant, ...]
    warehouses: tuple[Warehouse, ...]
    orders: tuple[Order, ...]
    shipments: tuple[Shipment, ...]
    inventory: tuple[InventoryLevel, ...]


def _refs(system: str, external_id: str) -> frozenset[ExternalReference]:
    return frozenset([ExternalReference(system=system, external_id=external_id)])


def _order(name: str, store: Store, status: OrderStatus, created_at: datetime,
           variant: Variant | None, amount: str, currency: str = "EUR") -> Order:  # fmt: skip
    return Order(
        id=cid(f"order:{name}"), store_id=store.id, customer_id=cid(f"customer:{name}"),
        status=status, source_status=f"src-{status.value}",
        items=(OrderItem(id=cid(f"item:{name}:1"), variant_id=None if variant is None else
                         variant.id, sku=None if variant is None else variant.sku,
                         title=f"Item {name}", quantity=Decimal("2"),
                         unit_price=Money(amount=Decimal(amount), currency=currency)),),
        total=Money(amount=Decimal(amount) * 2, currency=currency),
        created_at=created_at, external_refs=_refs("native-test", f"order-{name}"),
    )  # fmt: skip


def native_dataset() -> NativeDataset:
    company = Company(id=cid("company"), name="Native Test Company",
                      external_refs=_refs("native-test", "company"))  # fmt: skip
    store_a = Store(id=cid("store:a"), company_id=company.id, name="Store A", currency="EUR",
                    timezone="Europe/Berlin")  # fmt: skip
    store_b = Store(id=cid("store:b"), company_id=company.id, name="Store B",
                    currency="USD", timezone="America/New_York",
                    external_refs=_refs("native-test", "b"))  # fmt: skip
    product_a = Product(id=cid("product:a"), store_id=store_a.id, title="Tee",
                        status=ProductStatus.ACTIVE, source_status="published")  # fmt: skip
    product_b = Product(id=cid("product:b"), store_id=store_b.id, title="Cap",
                        status=ProductStatus.DRAFT)  # fmt: skip
    stocked = Variant(id=cid("variant:stocked"), product_id=product_a.id, title="Red M",
                      sku="TEE-RED-M")  # fmt: skip
    unstocked = Variant(id=cid("variant:unstocked"), product_id=product_b.id, sku="CAP-BLK")
    main = Warehouse(id=cid("warehouse:main"), company_id=company.id, name="Main")
    overflow = Warehouse(id=cid("warehouse:overflow"), company_id=company.id, name="Overflow")
    orders = (
        _order("a1", store_a, OrderStatus.PROCESSING, T0, stocked, "19.99"),
        _order("a2", store_a, OrderStatus.CONFIRMED, T0, stocked, "5.10"),  # same created_at
        _order("a3", store_a, OrderStatus.CANCELLED, T0 + timedelta(hours=3), None, "7.00"),
        _order("b1", store_b, OrderStatus.FULFILLED, T0 + timedelta(hours=1), unstocked,
               "12.50", "USD"),
        _order("b2", store_b, OrderStatus.PENDING, T0 + timedelta(days=1), unstocked, "3.00",
               "USD"),
    )  # fmt: skip
    a1, a2, a3, b1, b2 = orders
    shipments = (
        Shipment(id=cid("shipment:a1"), order_id=a1.id, status=ShipmentStatus.FAILED,
                 source_status="delivery_failed", courier_name="Courier One",
                 tracking_number="TRK-A1", shipped_at=T0 + timedelta(hours=2),
                 external_refs=_refs("native-test", "shipment-a1")),
        Shipment(id=cid("shipment:a2"), order_id=a2.id, status=ShipmentStatus.PENDING),
        Shipment(id=cid("shipment:a3"), order_id=a3.id, status=ShipmentStatus.SHIPPED,
                 shipped_at=T0 + timedelta(hours=2)),  # same shipped_at as a1
        Shipment(id=cid("shipment:b1"), order_id=b1.id, status=ShipmentStatus.DELIVERED,
                 shipped_at=T0 + timedelta(hours=1),
                 delivered_at=T0 + timedelta(days=2)),
        Shipment(id=cid("shipment:b2"), order_id=b2.id, status=ShipmentStatus.READY),
    )  # fmt: skip
    inventory = (
        InventoryLevel(id=cid("inventory:main"), variant_id=stocked.id, warehouse_id=main.id,
                       available=Decimal("12"), on_hand=Decimal("15"), reserved=Decimal("3"),
                       updated_at=T0),
        InventoryLevel(id=cid("inventory:overflow"), variant_id=stocked.id,
                       warehouse_id=overflow.id, available=Decimal("-4")),  # oversold
    )  # fmt: skip
    return NativeDataset(company=company, stores=(store_a, store_b),
                         products=(product_a, product_b), variants=(stocked, unstocked),
                         warehouses=(main, overflow), orders=orders, shipments=shipments,
                         inventory=inventory)  # fmt: skip


async def seed(store: PostgresCommerceStore, data: NativeDataset) -> None:
    """Write the dataset through the store's canonical upserts (parents first)."""
    await store.upsert_company(data.company)
    for each in data.stores:
        await store.upsert_store(each)
    for product in data.products:
        await store.upsert_product(product)
    for variant in data.variants:
        await store.upsert_variant(variant)
    for warehouse in data.warehouses:
        await store.upsert_warehouse(warehouse)
    for order in data.orders:
        await store.upsert_order(order)
    for shipment in data.shipments:
        await store.upsert_shipment(shipment)
    for level in data.inventory:
        await store.upsert_inventory_level(level)


def truncate_commerce(engine: sa.Engine) -> None:
    """Test-only: empty the nine commerce tables (one statement covers their FKs)."""
    names = ", ".join(f"product.{table.name}" for table in COMMERCE_TABLES)
    with engine.begin() as connection:
        connection.execute(sa.text(f"TRUNCATE {names}"))


def reset_and_seed(engine: sa.Engine, database_url: str, data: NativeDataset) -> None:
    truncate_commerce(engine)

    async def run() -> None:
        async_engine = create_product_engine(database_url, poolclass=NullPool)
        try:
            await seed(PostgresCommerceStore(create_session_factory(async_engine)), data)
        finally:
            await async_engine.dispose()

    asyncio.run(run())


def native_adapter(database_url: str) -> NativeCommerceAdapter:
    engine = create_product_engine(database_url, poolclass=NullPool)
    return NativeCommerceAdapter(PostgresCommerceStore(create_session_factory(engine)))


def unavailable_adapter() -> NativeCommerceAdapter:
    """Deterministic seam: every connection attempt fails immediately, and the failure
    carries a credential-bearing URL (it must never cross the boundary)."""

    async def refuse() -> object:
        raise ConnectionRefusedError(f"connect to {URL_MARKER} failed: {CREDENTIAL_MARKER}")

    engine = create_async_engine("postgresql+psycopg://", async_creator=refuse,
                                 poolclass=NullPool)  # fmt: skip
    return NativeCommerceAdapter(PostgresCommerceStore(create_session_factory(engine)))


def _corrupt(engine: sa.Engine, statement: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(statement), params)


def native_conformance_fixture(engine: sa.Engine, database_url: str) -> CommerceConformanceFixture:
    data = native_dataset()
    a1, _, _, b1, _ = data.orders
    shipment_a, *_ = data.shipments
    stocked, unstocked = data.variants
    store_a, store_b = data.stores
    adapter: Callable[[], CommerceIntegration] = lambda: native_adapter(database_url)  # noqa: E731

    def corrupted_order() -> tuple[CommerceIntegration, UUID]:
        _corrupt(engine, "UPDATE product.commerce_orders SET total_amount = :value WHERE id = :id",
                 value=f"not-a-number {RAW_MARKER} {CREDENTIAL_MARKER}", id=a1.id)  # fmt: skip
        return adapter(), a1.id

    def corrupted_shipment() -> tuple[CommerceIntegration, UUID]:
        _corrupt(engine, "UPDATE product.commerce_shipments SET external_refs = "
                 "CAST(:value AS jsonb) WHERE id = :id",
                 value=f'[{{"system": " ", "external_id": "{RAW_MARKER}"}}]',
                 id=shipment_a.id)  # fmt: skip
        return adapter(), shipment_a.id

    def corrupted_store() -> tuple[CommerceIntegration, UUID]:
        _corrupt(engine, "UPDATE product.commerce_stores SET external_refs = "
                 "CAST(:value AS jsonb) WHERE id = :id",
                 value=f'{{"raw": "{RAW_MARKER}"}}', id=store_a.id)  # fmt: skip
        return adapter(), store_a.id

    return CommerceConformanceFixture(
        name="native",
        adapter=adapter,
        store_a_id=store_a.id,
        store_b_id=store_b.id,
        order_a_id=a1.id,
        order_b_id=b1.id,
        shipment_a_id=shipment_a.id,
        shipment_b_id=cid("shipment:b1"),
        variant_id=stocked.id,
        warehouse_id=cid("warehouse:main"),
        variant_without_stock_id=unstocked.id,
        unavailable_adapter=unavailable_adapter,
        corrupted_order=corrupted_order,
        corrupted_shipment=corrupted_shipment,
        corrupted_store=corrupted_store,
        leak_markers=MARKERS,
    )
