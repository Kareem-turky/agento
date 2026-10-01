"""Task 031: the Product-owned canonical commerce store on the real, migrated PostgreSQL.

``PostgresCommerceStore`` (upserts, strict reads, error boundary) and
``NativeCommerceAdapter`` over it: exact round-trips, atomic item replacement,
idempotent upserts, FK safety, fail-closed corrupted rows, SQL-side filtering with no
per-order queries, and durability across a brand-new engine/store/adapter.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.pool import NullPool

from app.commerce.domain import (
    ExternalReference,
    InventoryLevel,
    Money,
    Order,
    OrderItem,
    OrderStatus,
    ShipmentStatus,
    Store,
)
from app.commerce.store import (
    CommerceStoreDataError,
    CommerceStoreNotFoundError,
    CommerceStoreWriteError,
)
from app.integrations.commerce import IntegrationDataError, OrderQuery, ShipmentQuery
from app.integrations.commerce.native import NativeCommerceAdapter
from app.persistence import PostgresCommerceStore, create_product_engine, create_session_factory
from tests.integrations.native_conformance import (
    NativeDataset,
    cid,
    native_dataset,
    reset_and_seed,
    truncate_commerce,
)

pytestmark = pytest.mark.integration

PRECISE = Decimal("0.12345678901234567890123456789")


def run[T](database_url: str, body: Callable[[PostgresCommerceStore], Awaitable[T]]) -> T:
    """Run ``body`` with a store on its OWN fresh engine, disposed afterwards."""

    async def main() -> T:
        engine = create_product_engine(database_url, poolclass=NullPool)
        try:
            return await body(PostgresCommerceStore(create_session_factory(engine)))
        finally:
            await engine.dispose()

    return asyncio.run(main())


@pytest.fixture
def data(migrated: str, engine: sa.Engine) -> NativeDataset:
    dataset = native_dataset()
    reset_and_seed(engine, migrated, dataset)
    return dataset


def counts(engine: sa.Engine) -> dict[str, int]:
    tables = ("companies", "stores", "products", "variants", "warehouses", "orders",
              "order_items", "shipments", "inventory_levels")  # fmt: skip
    with engine.connect() as connection:
        return {t: connection.execute(sa.text(f"SELECT count(*) FROM product.commerce_{t}"))  # noqa: S608 - fixed names
                .scalar_one() for t in tables}  # fmt: skip


# ----- round-trips ------------------------------------------------------------------------


def test_every_entity_round_trips_exactly(data: NativeDataset, migrated: str) -> None:
    async def body(store: PostgresCommerceStore) -> dict[str, Any]:
        return {
            "stores": [await store.get_store(s.id) for s in data.stores],
            "orders": [await store.get_order(o.id) for o in data.orders],
            "shipments": [await store.get_shipment(s.id) for s in data.shipments],
            "inventory": await store.get_inventory(data.variants[0].id, None),
        }

    read = run(migrated, body)
    assert read["stores"] == list(data.stores)
    assert read["orders"] == list(data.orders)
    assert read["shipments"] == list(data.shipments)
    assert read["inventory"] == tuple(sorted(data.inventory, key=lambda lv: lv.warehouse_id))
    # source_status and external references persist (deterministic JSON arrays).
    assert read["orders"][0].source_status == "src-processing"
    assert read["shipments"][0].source_status == "delivery_failed"
    assert read["orders"][0].external_refs == data.orders[0].external_refs


def test_company_product_variant_warehouse_rows_are_canonical(
    data: NativeDataset, engine: sa.Engine
) -> None:
    with engine.connect() as connection:
        company = (
            connection.execute(
                sa.text("SELECT id, name, external_refs FROM product.commerce_companies")
            )
            .mappings()
            .one()
        )
        products = (
            connection.execute(
                sa.text(
                    "SELECT id, store_id, status, source_status FROM product.commerce_products "
                    "ORDER BY title DESC"
                )
            )
            .mappings()
            .all()
        )
        variants = connection.execute(
            sa.text(
                "SELECT v.id, v.sku, p.store_id FROM product.commerce_variants v "
                "JOIN product.commerce_products p ON p.id = v.product_id ORDER BY v.sku"
            )
        ).all()
        warehouses = connection.execute(
            sa.text("SELECT id, company_id, name FROM product.commerce_warehouses ORDER BY name")
        ).all()
    assert (company["id"], company["name"]) == (data.company.id, "Native Test Company")
    assert company["external_refs"] == [{"system": "native-test", "external_id": "company"}]
    assert [(p["id"], p["status"], p["source_status"]) for p in products] == [
        (data.products[0].id, "active", "published"), (data.products[1].id, "draft", None),
    ]  # fmt: skip
    # A variant belongs to a persisted product (and through it to a store).
    assert variants == [(cid("variant:unstocked"), "CAP-BLK", data.stores[1].id),
                        (cid("variant:stocked"), "TEE-RED-M", data.stores[0].id)]  # fmt: skip
    assert [(w.id, w.company_id) for w in warehouses] == [
        (w.id, data.company.id) for w in data.warehouses]  # fmt: skip


def test_items_keep_their_exact_tuple_order(data: NativeDataset, migrated: str) -> None:
    base = data.orders[0]
    items = tuple(
        OrderItem(id=uuid4(), title=f"line {n}", quantity=Decimal(n), unit_price=base.total)
        for n in (9, 1, 5, 3)  # deliberately not id- or title-sorted
    )  # fmt: skip
    order = base.model_copy(update={"items": items})

    async def body(store: PostgresCommerceStore) -> Order:
        await store.upsert_order(order)
        return await store.get_order(order.id)

    assert run(migrated, body).items == items


def test_order_upsert_replaces_the_item_collection_atomically(
    data: NativeDataset, migrated: str, engine: sa.Engine
) -> None:
    price = Money(amount=Decimal("1"), currency="EUR")
    a, b, c = (OrderItem(id=uuid4(), title=t, quantity=Decimal(1), unit_price=price)
               for t in "ABC")  # fmt: skip
    first = data.orders[0].model_copy(update={"items": (a, b)})
    second = first.model_copy(update={"items": (b, c), "status": OrderStatus.FULFILLED})

    async def body(store: PostgresCommerceStore) -> tuple[Order, Order]:
        await store.upsert_order(first)
        one = await store.get_order(first.id)
        await store.upsert_order(second)
        return one, await store.get_order(first.id)

    before, after = run(migrated, body)
    assert before.items == (a, b)
    assert after.items == (b, c) and after.status is OrderStatus.FULFILLED
    with engine.connect() as connection:
        rows = connection.execute(sa.text(
            "SELECT id, position FROM product.commerce_order_items WHERE order_id = :o "
            "ORDER BY position"), {"o": first.id}).all()  # fmt: skip
    assert rows == [(b.id, 0), (c.id, 1)]  # no stale A, no duplicate B


def test_a_rejected_order_upsert_changes_nothing(data: NativeDataset, migrated: str) -> None:
    original = data.orders[0]
    unknown_variant = OrderItem(id=uuid4(), variant_id=uuid4(), title="ghost",
                                quantity=Decimal(1), unit_price=original.total)  # fmt: skip
    broken = original.model_copy(update={"items": (unknown_variant,),
                                         "status": OrderStatus.COMPLETED})  # fmt: skip

    async def body(store: PostgresCommerceStore) -> Order:
        with pytest.raises(CommerceStoreWriteError) as error:
            await store.upsert_order(broken)
        assert str(error.value) == f"order {original.id} could not be stored"
        return await store.get_order(original.id)

    assert run(migrated, body) == original  # no partially replaced order


def test_high_precision_decimals_and_negative_stock_round_trip(
    data: NativeDataset, migrated: str, engine: sa.Engine
) -> None:
    price = Money(amount=PRECISE, currency="EUR")
    item = OrderItem(id=uuid4(), title="precise", quantity=Decimal("1.000000000000000000001"),
                     unit_price=price)  # fmt: skip
    order = data.orders[0].model_copy(update={"items": (item,), "total": price})
    level = InventoryLevel(id=uuid4(), variant_id=data.variants[1].id,
                           warehouse_id=data.warehouses[0].id, available=Decimal("-3.50"),
                           on_hand=Decimal("-0.000000000000000000000000000001"),
                           reserved=Decimal("1E+2"))  # fmt: skip

    async def body(store: PostgresCommerceStore) -> tuple[Order, tuple[InventoryLevel, ...]]:
        await store.upsert_order(order)
        await store.upsert_inventory_level(level)
        return await store.get_order(order.id), await store.get_inventory(level.variant_id, None)

    read, levels = run(migrated, body)
    assert read.total.amount == PRECISE and str(read.total.amount) == str(PRECISE)
    assert str(read.items[0].quantity) == "1.000000000000000000001"
    assert levels == (level,)
    assert (str(levels[0].available), str(levels[0].reserved)) == ("-3.50", "1E+2")
    with engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT total_amount FROM product.commerce_orders WHERE id = :o"),
            {"o": order.id},
        )
    assert stored.scalar_one() == "0.12345678901234567890123456789"  # exact text, no float


def test_repeated_upserts_are_idempotent_by_canonical_id(
    data: NativeDataset, migrated: str, engine: sa.Engine
) -> None:
    from tests.integrations.native_conformance import seed

    before = counts(engine)
    renamed = data.stores[0].model_copy(update={"name": "Store A renamed", "external_refs":
        frozenset([ExternalReference(system="native-test", external_id="a2")])})  # fmt: skip

    async def body(store: PostgresCommerceStore) -> Store:
        await seed(store, data)  # the whole dataset again
        await seed(store, data)
        await store.upsert_store(renamed)
        return await store.get_store(renamed.id)

    assert run(migrated, body) == renamed
    assert counts(engine) == before  # nothing duplicated
    assert before == {"companies": 1, "stores": 2, "products": 2, "variants": 2,
                      "warehouses": 2, "orders": 5, "order_items": 5, "shipments": 5,
                      "inventory_levels": 2}  # fmt: skip


def test_unknown_relationships_are_rejected_safely(data: NativeDataset, migrated: str) -> None:
    orphan_store = data.stores[0].model_copy(update={"id": uuid4(), "company_id": uuid4()})
    orphan_shipment = data.shipments[0].model_copy(update={"id": uuid4(), "order_id": uuid4()})
    orphan_level = data.inventory[0].model_copy(update={"id": uuid4(), "warehouse_id": uuid4()})

    async def body(store: PostgresCommerceStore) -> list[str]:
        messages = []
        for write, model in ((store.upsert_store, orphan_store),
                             (store.upsert_shipment, orphan_shipment),
                             (store.upsert_inventory_level, orphan_level)):  # fmt: skip
            with pytest.raises(CommerceStoreWriteError) as error:
                await write(model)  # type: ignore[arg-type]
            assert error.value.__cause__ is None and error.value.__suppress_context__
            messages.append(str(error.value))
        return messages

    messages = run(migrated, body)
    assert messages == [f"store {orphan_store.id} could not be stored",
                        f"shipment {orphan_shipment.id} could not be stored",
                        f"inventory_level {orphan_level.id} could not be stored"]  # fmt: skip
    for text in messages:
        for leak in ("INSERT", "violates", "foreign key", "psycopg", "postgresql", "fk_"):
            assert leak not in text


def test_not_found_and_unknown_scopes(data: NativeDataset, migrated: str) -> None:
    missing = uuid4()

    async def body(store: PostgresCommerceStore) -> list[Any]:
        found = []
        for call, entity in ((store.get_store, "store"), (store.get_order, "order"),
                             (store.get_shipment, "shipment")):  # fmt: skip
            with pytest.raises(CommerceStoreNotFoundError) as error:
                await call(missing)
            found.append((error.value.entity, error.value.entity_id) == (entity, missing))
        scopes = ((missing, None, "variant"), (data.variants[0].id, missing, "warehouse"))
        for variant, warehouse, entity in scopes:
            with pytest.raises(CommerceStoreNotFoundError) as error:
                await store.get_inventory(variant, warehouse)
            found.append(error.value.entity == entity)
        found.append(await store.get_inventory(data.variants[1].id, None) == ())
        found.append(await store.list_orders(store_id=missing, statuses=frozenset(),
                                             created_from=None, created_to=None,
                                             limit=None) == ())  # fmt: skip
        found.append(await store.list_shipments(order_id=missing, store_id=None,
                                                statuses=frozenset(), shipped_from=None,
                                                shipped_to=None, limit=None) == ())  # fmt: skip
        return found

    assert all(run(migrated, body))


@pytest.mark.parametrize(
    ("table", "column", "value", "read"),
    [
        ("commerce_orders", "total_amount", "1,5 EUR", "order"),
        ("commerce_order_items", "quantity", "-2", "order"),  # quantity must be > 0
        ("commerce_orders", "external_refs", '"not-an-array"', "order"),
        ("commerce_shipments", "external_refs", "[1, 2]", "shipment"),
        ("commerce_inventory_levels", "available", "NaN", "inventory"),
        ("commerce_stores", "external_refs", '[{"system": "x"}]', "store"),
    ],
)
def test_corrupted_rows_fail_closed_without_leaking(
    data: NativeDataset, migrated: str, engine: sa.Engine, table: str, column: str,
    value: str, read: str,
) -> None:  # fmt: skip
    cast = "CAST(:value AS jsonb)" if column == "external_refs" else ":value"
    with engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE product.{table} SET {column} = {cast}"),  # noqa: S608 - test parameters
                           {"value": value})  # fmt: skip
    calls = {
        "order": lambda s: s.get_order(data.orders[0].id),
        "shipment": lambda s: s.get_shipment(data.shipments[0].id),
        "inventory": lambda s: s.get_inventory(data.variants[0].id, None),
        "store": lambda s: s.get_store(data.stores[0].id),
    }

    async def body(store: PostgresCommerceStore) -> list[BaseException]:
        errors: list[BaseException] = []
        with pytest.raises(CommerceStoreDataError) as error:
            await calls[read](store)
        errors.append(error.value)
        adapter = NativeCommerceAdapter(store)
        with pytest.raises(IntegrationDataError) as product_error:
            await calls[read](adapter) if read != "inventory" else \
                await adapter.get_inventory(data.variants[0].id)  # fmt: skip
        errors.append(product_error.value)
        return errors

    for error in run(migrated, body):
        text = " ".join([str(error), repr(error), repr(vars(error))])
        assert value not in text and "ValidationError" not in text
        assert error.__cause__ is None


# ----- SQL execution: filters/limit in SQL, no per-order item queries ----------------------


def test_list_reads_filter_and_limit_in_sql_with_batched_items(
    data: NativeDataset, migrated: str
) -> None:
    statements: list[str] = []

    async def body(store: PostgresCommerceStore) -> tuple[Any, ...]:
        adapter = NativeCommerceAdapter(store)
        statements.clear()
        everything = await adapter.list_orders()
        all_orders_statements = len(statements)
        statements.clear()
        limited = await adapter.list_orders(OrderQuery(store_id=data.stores[0].id, limit=2))
        order_sql = list(statements)
        statements.clear()
        scoped = await adapter.list_shipments(ShipmentQuery(
            store_id=data.stores[0].id, statuses=frozenset({ShipmentStatus.FAILED,
            ShipmentStatus.SHIPPED}), limit=5))  # fmt: skip
        return everything, all_orders_statements, limited, order_sql, scoped, list(statements)

    def listen(engine: Any) -> None:
        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            statements.append(statement)

    async def main() -> tuple[Any, ...]:
        engine = create_product_engine(migrated, poolclass=NullPool)
        listen(engine)
        try:
            return await body(PostgresCommerceStore(create_session_factory(engine)))
        finally:
            await engine.dispose()

    everything, n_all, limited, order_sql, scoped, shipment_sql = asyncio.run(main())
    assert len(everything) == 5
    assert n_all == 2  # ONE order query + ONE batched item query (no N+1)
    a_orders = [o for o in data.orders if o.store_id == data.stores[0].id]
    expected = sorted(a_orders, key=lambda o: (o.created_at, str(o.id)))[:2]
    assert limited == tuple(expected)
    assert "WHERE" in order_sql[0] and "LIMIT" in order_sql[0] and "ORDER BY" in order_sql[0]
    assert "IN" in order_sql[1]  # the batched item query
    (sql,) = shipment_sql
    assert "JOIN product.commerce_orders" in sql and "LIMIT" in sql
    assert "NULLS LAST" in sql
    assert [s.id for s in scoped] == sorted(
        (cid("shipment:a1"), cid("shipment:a3")), key=str
    )  # same shipped_at -> id order


def test_shipped_at_ordering_puts_unshipped_last(data: NativeDataset, migrated: str) -> None:
    async def body(store: PostgresCommerceStore) -> list[UUID]:
        shipments = await NativeCommerceAdapter(store).list_shipments()
        return [s.id for s in shipments]

    ids = run(migrated, body)
    unshipped = {cid("shipment:a2"), cid("shipment:b2")}
    assert set(ids[-2:]) == unshipped and ids[-2:] == sorted(unshipped, key=str)
    assert ids[0] == cid("shipment:b1")  # earliest shipped_at


# ----- durability --------------------------------------------------------------------------


def test_state_survives_a_brand_new_engine_store_and_adapter(
    migrated: str, engine: sa.Engine
) -> None:
    truncate_commerce(engine)
    dataset = native_dataset()
    from tests.integrations.native_conformance import seed

    run(migrated, lambda store: seed(store, dataset))  # engine #1, then disposed

    async def read(store: PostgresCommerceStore) -> tuple[Any, ...]:
        adapter = NativeCommerceAdapter(store)  # a NEW adapter on engine #2
        return (await adapter.get_store(dataset.stores[0].id),
                await adapter.get_order(dataset.orders[0].id),
                await adapter.get_shipment(dataset.shipments[0].id),
                await adapter.get_inventory(dataset.variants[0].id))  # fmt: skip

    store, order, shipment, inventory = run(migrated, read)
    assert (store, order, shipment, inventory) == (
        dataset.stores[0],
        dataset.orders[0],
        dataset.shipments[0],
        tuple(sorted(dataset.inventory, key=lambda lv: lv.warehouse_id)),
    )
    # Instants are preserved exactly (PostgreSQL returns them in its session time zone).
    assert order.created_at == dataset.orders[0].created_at
    assert order.created_at.utcoffset() == timedelta(0)


def test_offset_timestamps_keep_their_instant(data: NativeDataset, migrated: str) -> None:
    cairo = timezone(timedelta(hours=2))
    order = data.orders[0].model_copy(update={
        "created_at": datetime(2031, 5, 4, 10, 0, tzinfo=cairo),
        "updated_at": datetime(2031, 5, 4, 9, 30, tzinfo=UTC)})  # fmt: skip

    async def body(store: PostgresCommerceStore) -> Order:
        await store.upsert_order(order)
        return await store.get_order(order.id)

    read = run(migrated, body)
    assert read == order and read.created_at == datetime(2031, 5, 4, 8, 0, tzinfo=UTC)
