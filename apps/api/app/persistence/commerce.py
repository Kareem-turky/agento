"""PostgreSQL implementation of the Product-owned canonical commerce store (Task 031).

Tables ``product.commerce_*`` are owned by migration 0003 and only mirrored here (never
created). ``PostgresCommerceStore`` implements ``app.commerce.store.CommerceStoreReader``
for ``NativeCommerceAdapter`` and offers narrow canonical upserts (no HTTP surface: an
ingestion boundary is a later task).

- Identity: the canonical UUID. An upsert replaces the stored state of that id and
  never duplicates it. ``upsert_order`` replaces the order row AND its whole item
  collection (``position`` = index in ``Order.items``) in ONE transaction.
- Transactions: one public call = one short transaction; no session is held or exposed.
- Exactness: decimals are stored as exact decimal text (never through float) and
  external references as deterministic JSON arrays sorted by (system, external_id).
- Reads: list filters, ordering and ``limit`` run in SQL; a shipment's store is its
  parent order's store (a SQL join). Order items are loaded with ONE batched query per
  read (no per-order query). Every row is rebuilt through the canonical Pydantic
  models; invalid stored data fails closed (``CommerceStoreDataError``).
- Errors: only ``app.commerce.store`` errors with entity names, canonical ids or fixed
  messages. SQL, URLs, credentials, driver exceptions and stored values never cross
  this boundary (exceptions are not chained).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.domain import (
    Company,
    InventoryLevel,
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
from app.commerce.domain.common import ExternalReferences
from app.commerce.store import (
    CommerceStoreDataError,
    CommerceStoreNotFoundError,
    CommerceStoreUnavailableError,
    CommerceStoreWriteError,
)
from app.persistence.database import PRODUCT_SCHEMA, product_metadata

_REFERENCES: TypeAdapter[Any] = TypeAdapter(ExternalReferences)


def _refs() -> sa.Column[Any]:
    return sa.Column("external_refs", JSONB(), nullable=False)


def _fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{PRODUCT_SCHEMA}.{parent}.id"], name=f"fk_{table}_{column}"
    )


def _status(table: str, values: Iterable[str]) -> sa.CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"status IN ({listed})", name=f"ck_{table}_status")


def _currency(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} ~ '^[A-Z]{{3}}$'", name=f"ck_{table}_{column}")


# Mirrors migration 0003 (``alembic check`` and the vocabulary test keep them in step).
commerce_companies = sa.Table(
    "commerce_companies",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_companies"),
)
commerce_stores = sa.Table(
    "commerce_stores",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Uuid(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("timezone", sa.Text(), nullable=False),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_stores"),
    _fk("commerce_stores", "company_id", "commerce_companies"),
    _currency("commerce_stores", "currency"),
)
commerce_products = sa.Table(
    "commerce_products",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("store_id", sa.Uuid(), nullable=False),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("source_status", sa.Text(), nullable=True),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_products"),
    _fk("commerce_products", "store_id", "commerce_stores"),
    _status("commerce_products", (s.value for s in ProductStatus)),
)
commerce_variants = sa.Table(
    "commerce_variants",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("product_id", sa.Uuid(), nullable=False),
    sa.Column("title", sa.Text(), nullable=True),
    sa.Column("sku", sa.Text(), nullable=True),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_variants"),
    _fk("commerce_variants", "product_id", "commerce_products"),
)
commerce_warehouses = sa.Table(
    "commerce_warehouses",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("company_id", sa.Uuid(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_warehouses"),
    _fk("commerce_warehouses", "company_id", "commerce_companies"),
)
commerce_orders = sa.Table(
    "commerce_orders",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("store_id", sa.Uuid(), nullable=False),
    sa.Column("customer_id", sa.Uuid(), nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("source_status", sa.Text(), nullable=True),
    sa.Column("total_amount", sa.Text(), nullable=False),
    sa.Column("total_currency", sa.String(3), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_orders"),
    _fk("commerce_orders", "store_id", "commerce_stores"),
    _status("commerce_orders", (s.value for s in OrderStatus)),
    _currency("commerce_orders", "total_currency"),
    sa.Index("ix_commerce_orders_created_at_id", "created_at", "id"),
    sa.Index("ix_commerce_orders_store_id_created_at_id", "store_id", "created_at", "id"),
)
commerce_order_items = sa.Table(
    "commerce_order_items",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("order_id", sa.Uuid(), nullable=False),
    sa.Column("position", sa.Integer(), nullable=False),
    sa.Column("variant_id", sa.Uuid(), nullable=True),
    sa.Column("sku", sa.Text(), nullable=True),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("quantity", sa.Text(), nullable=False),
    sa.Column("unit_price_amount", sa.Text(), nullable=False),
    sa.Column("unit_price_currency", sa.String(3), nullable=False),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_order_items"),
    _fk("commerce_order_items", "order_id", "commerce_orders"),
    _fk("commerce_order_items", "variant_id", "commerce_variants"),
    sa.UniqueConstraint("order_id", "position", name="uq_commerce_order_items_position"),
    sa.CheckConstraint("position >= 0", name="ck_commerce_order_items_position"),
    _currency("commerce_order_items", "unit_price_currency"),
)
commerce_shipments = sa.Table(
    "commerce_shipments",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("order_id", sa.Uuid(), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("source_status", sa.Text(), nullable=True),
    sa.Column("courier_name", sa.Text(), nullable=True),
    sa.Column("tracking_number", sa.Text(), nullable=True),
    sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    _refs(),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_shipments"),
    _fk("commerce_shipments", "order_id", "commerce_orders"),
    _status("commerce_shipments", (s.value for s in ShipmentStatus)),
    sa.Index("ix_commerce_shipments_order_id", "order_id"),
    sa.Index("ix_commerce_shipments_shipped_at_id", "shipped_at", "id"),
)
commerce_inventory_levels = sa.Table(
    "commerce_inventory_levels",
    product_metadata,
    sa.Column("id", sa.Uuid(), nullable=False),
    sa.Column("variant_id", sa.Uuid(), nullable=False),
    sa.Column("warehouse_id", sa.Uuid(), nullable=False),
    sa.Column("available", sa.Text(), nullable=False),
    sa.Column("on_hand", sa.Text(), nullable=True),
    sa.Column("reserved", sa.Text(), nullable=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("id", name="pk_commerce_inventory_levels"),
    _fk("commerce_inventory_levels", "variant_id", "commerce_variants"),
    _fk("commerce_inventory_levels", "warehouse_id", "commerce_warehouses"),
    sa.Index("ix_commerce_inventory_levels_variant_id_warehouse_id", "variant_id", "warehouse_id"),
)

COMMERCE_TABLES = (
    commerce_companies, commerce_stores, commerce_products, commerce_variants,
    commerce_warehouses, commerce_orders, commerce_order_items, commerce_shipments,
    commerce_inventory_levels,
)  # fmt: skip

# Connection-level failures: the store cannot answer. Anything else from the database
# while writing is a rejected write; while reading, also "unavailable" (it cannot answer).
_UNAVAILABLE = (sa_exc.OperationalError, sa_exc.InterfaceError, OSError, TimeoutError)


# ----- canonical -> row -------------------------------------------------------------------


def _decimal(value: object) -> str | None:
    # str(Decimal) is exact (no rounding, no float); the domain already rejected floats.
    return None if value is None else str(value)


def _references(model: Any) -> list[dict[str, str]]:
    return _REFERENCES.dump_python(model.external_refs, mode="json")


def _company_row(company: Company) -> dict[str, Any]:
    return {"id": company.id, "name": company.name, "external_refs": _references(company)}


def _store_row(store: Store) -> dict[str, Any]:
    return {"id": store.id, "company_id": store.company_id, "name": store.name,
            "currency": store.currency, "timezone": store.timezone,
            "external_refs": _references(store)}  # fmt: skip


def _product_row(product: Product) -> dict[str, Any]:
    return {"id": product.id, "store_id": product.store_id, "title": product.title,
            "status": product.status.value, "source_status": product.source_status,
            "external_refs": _references(product)}  # fmt: skip


def _variant_row(variant: Variant) -> dict[str, Any]:
    return {"id": variant.id, "product_id": variant.product_id, "title": variant.title,
            "sku": variant.sku, "external_refs": _references(variant)}  # fmt: skip


def _warehouse_row(warehouse: Warehouse) -> dict[str, Any]:
    return {"id": warehouse.id, "company_id": warehouse.company_id, "name": warehouse.name,
            "external_refs": _references(warehouse)}  # fmt: skip


def _order_row(order: Order) -> dict[str, Any]:
    return {"id": order.id, "store_id": order.store_id, "customer_id": order.customer_id,
            "status": order.status.value, "source_status": order.source_status,
            "total_amount": _decimal(order.total.amount),
            "total_currency": order.total.currency, "created_at": order.created_at,
            "updated_at": order.updated_at, "external_refs": _references(order)}  # fmt: skip


def _item_row(order_id: UUID, position: int, item: OrderItem) -> dict[str, Any]:
    return {"id": item.id, "order_id": order_id, "position": position,
            "variant_id": item.variant_id, "sku": item.sku, "title": item.title,
            "quantity": _decimal(item.quantity),
            "unit_price_amount": _decimal(item.unit_price.amount),
            "unit_price_currency": item.unit_price.currency,
            "external_refs": _references(item)}  # fmt: skip


def _shipment_row(shipment: Shipment) -> dict[str, Any]:
    return {"id": shipment.id, "order_id": shipment.order_id,
            "status": shipment.status.value, "source_status": shipment.source_status,
            "courier_name": shipment.courier_name,
            "tracking_number": shipment.tracking_number, "shipped_at": shipment.shipped_at,
            "delivered_at": shipment.delivered_at,
            "external_refs": _references(shipment)}  # fmt: skip


def _inventory_row(level: InventoryLevel) -> dict[str, Any]:
    return {"id": level.id, "variant_id": level.variant_id,
            "warehouse_id": level.warehouse_id, "available": _decimal(level.available),
            "on_hand": _decimal(level.on_hand), "reserved": _decimal(level.reserved),
            "updated_at": level.updated_at}  # fmt: skip


# ----- row -> canonical (strict) ----------------------------------------------------------


def _store(row: sa.RowMapping) -> Store:
    return Store.model_validate(dict(row))


def _shipment(row: sa.RowMapping) -> Shipment:
    return Shipment.model_validate(dict(row))


def _inventory(row: sa.RowMapping) -> InventoryLevel:
    return InventoryLevel.model_validate(dict(row))


def _item(row: sa.RowMapping) -> OrderItem:
    return OrderItem.model_validate({
        "id": row["id"], "variant_id": row["variant_id"], "sku": row["sku"],
        "title": row["title"], "quantity": row["quantity"],
        "unit_price": {"amount": row["unit_price_amount"],
                       "currency": row["unit_price_currency"]},
        "external_refs": row["external_refs"],
    })  # fmt: skip


def _order(row: sa.RowMapping, items: Sequence[OrderItem]) -> Order:
    return Order.model_validate({
        "id": row["id"], "store_id": row["store_id"], "customer_id": row["customer_id"],
        "status": row["status"], "source_status": row["source_status"], "items": tuple(items),
        "total": {"amount": row["total_amount"], "currency": row["total_currency"]},
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "external_refs": row["external_refs"],
    })  # fmt: skip


_INVALID = (ValidationError, ValueError, TypeError, KeyError, ArithmeticError)

_o = commerce_orders.c
_i = commerce_order_items.c
_s = commerce_shipments.c
_v = commerce_inventory_levels.c


class PostgresCommerceStore:
    """The Product-owned canonical commerce store on PostgreSQL. See the module
    docstring. Receives its session factory explicitly; holds no session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # ----- writes (narrow canonical upserts; one short transaction each) ------------------

    async def upsert_company(self, company: Company) -> None:
        await self._upsert(commerce_companies, _company_row(company), "company", company.id)

    async def upsert_store(self, store: Store) -> None:
        await self._upsert(commerce_stores, _store_row(store), "store", store.id)

    async def upsert_product(self, product: Product) -> None:
        await self._upsert(commerce_products, _product_row(product), "product", product.id)

    async def upsert_variant(self, variant: Variant) -> None:
        await self._upsert(commerce_variants, _variant_row(variant), "variant", variant.id)

    async def upsert_warehouse(self, warehouse: Warehouse) -> None:
        await self._upsert(commerce_warehouses, _warehouse_row(warehouse), "warehouse",
                           warehouse.id)  # fmt: skip

    async def upsert_shipment(self, shipment: Shipment) -> None:
        await self._upsert(commerce_shipments, _shipment_row(shipment), "shipment", shipment.id)

    async def upsert_inventory_level(self, level: InventoryLevel) -> None:
        await self._upsert(commerce_inventory_levels, _inventory_row(level), "inventory_level",
                           level.id)  # fmt: skip

    async def upsert_order(self, order: Order) -> None:
        """Upsert the order and REPLACE its whole item collection, atomically."""
        items = [_item_row(order.id, position, item) for position, item in enumerate(order.items)]
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(_upsert_statement(commerce_orders, _order_row(order)))
                await session.execute(sa.delete(commerce_order_items)
                                      .where(_i.order_id == order.id))  # fmt: skip
                await session.execute(sa.insert(commerce_order_items), items)
        except _UNAVAILABLE:
            raise CommerceStoreUnavailableError() from None
        except sa_exc.SQLAlchemyError:
            raise CommerceStoreWriteError("order", order.id) from None

    async def _upsert(self, table: sa.Table, row: dict[str, Any], entity: str,
                      entity_id: UUID) -> None:  # fmt: skip
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(_upsert_statement(table, row))
        except _UNAVAILABLE:
            raise CommerceStoreUnavailableError() from None
        except sa_exc.SQLAlchemyError:
            raise CommerceStoreWriteError(entity, entity_id) from None

    # ----- reads (CommerceStoreReader) -----------------------------------------------------

    async def get_store(self, store_id: UUID) -> Store:
        columns = _columns(commerce_stores, "company_id", "name", "currency", "timezone")
        query = sa.select(*columns).where(commerce_stores.c.id == store_id)
        async with self._read() as session:
            row = (await session.execute(query)).mappings().first()
        if row is None:
            raise CommerceStoreNotFoundError("store", store_id)
        return _mapped("store", lambda: _store(row))

    async def get_order(self, order_id: UUID) -> Order:
        orders = await self._orders(sa.select(commerce_orders).where(_o.id == order_id))
        if not orders:
            raise CommerceStoreNotFoundError("order", order_id)
        return orders[0]

    async def list_orders(
        self,
        *,
        store_id: UUID | None,
        statuses: frozenset[OrderStatus],
        created_from: datetime | None,
        created_to: datetime | None,
        limit: int | None,
    ) -> tuple[Order, ...]:
        query = sa.select(commerce_orders)
        if store_id is not None:
            query = query.where(_o.store_id == store_id)
        if statuses:
            query = query.where(_o.status.in_(sorted(s.value for s in statuses)))
        if created_from is not None:
            query = query.where(_o.created_at >= created_from)
        if created_to is not None:
            query = query.where(_o.created_at < created_to)
        query = query.order_by(_o.created_at.asc(), _o.id.asc())
        if limit is not None:
            query = query.limit(limit)
        return await self._orders(query)

    async def get_shipment(self, shipment_id: UUID) -> Shipment:
        query = sa.select(commerce_shipments).where(_s.id == shipment_id)
        async with self._read() as session:
            row = (await session.execute(query)).mappings().first()
        if row is None:
            raise CommerceStoreNotFoundError("shipment", shipment_id)
        return _mapped("shipment", lambda: _shipment(row))

    async def list_shipments(
        self,
        *,
        order_id: UUID | None,
        store_id: UUID | None,
        statuses: frozenset[ShipmentStatus],
        shipped_from: datetime | None,
        shipped_to: datetime | None,
        limit: int | None,
    ) -> tuple[Shipment, ...]:
        query = sa.select(commerce_shipments)
        if store_id is not None:
            # The store is the PARENT order's store, enforced in SQL.
            query = query.join(commerce_orders, _o.id == _s.order_id).where(_o.store_id == store_id)
        if order_id is not None:
            query = query.where(_s.order_id == order_id)
        if statuses:
            query = query.where(_s.status.in_(sorted(s.value for s in statuses)))
        # A time bound only ever matches shipped shipments (NULL never compares true).
        if shipped_from is not None:
            query = query.where(_s.shipped_at >= shipped_from)
        if shipped_to is not None:
            query = query.where(_s.shipped_at < shipped_to)
        query = query.order_by(_s.shipped_at.asc().nulls_last(), _s.id.asc())
        if limit is not None:
            query = query.limit(limit)
        async with self._read() as session:
            rows = (await session.execute(query)).mappings().all()
        return _mapped("shipment", lambda: tuple(_shipment(row) for row in rows))

    async def get_inventory(
        self, variant_id: UUID, warehouse_id: UUID | None
    ) -> tuple[InventoryLevel, ...]:
        query = sa.select(commerce_inventory_levels).where(_v.variant_id == variant_id)
        if warehouse_id is not None:
            query = query.where(_v.warehouse_id == warehouse_id)
        query = query.order_by(_v.warehouse_id.asc(), _v.id.asc())
        variants, warehouses = commerce_variants.c, commerce_warehouses.c
        variant_known = sa.select(variants.id).where(variants.id == variant_id)
        warehouse_known = sa.select(warehouses.id).where(warehouses.id == warehouse_id)
        async with self._read() as session:
            if (await session.execute(variant_known)).first() is None:
                raise CommerceStoreNotFoundError("variant", variant_id)
            if warehouse_id is not None and not (await session.execute(warehouse_known)).first():
                raise CommerceStoreNotFoundError("warehouse", warehouse_id)
            rows = (await session.execute(query)).mappings().all()
        return _mapped("inventory_level", lambda: tuple(_inventory(row) for row in rows))

    # ----- internals ------------------------------------------------------------------------

    def _read(self) -> "_ReadSession":
        return _ReadSession(self._sessions)

    async def _orders(self, query: sa.Select[Any]) -> tuple[Order, ...]:
        """The selected order rows, in query order, plus ONE batched item query."""
        async with self._read() as session:
            rows = (await session.execute(query)).mappings().all()
            ids = [row["id"] for row in rows]
            item_rows = (
                (await session.execute(
                    sa.select(commerce_order_items).where(_i.order_id.in_(ids))
                    .order_by(_i.order_id, _i.position)
                )).mappings().all() if ids else []
            )  # fmt: skip

        def build() -> tuple[Order, ...]:
            items: dict[UUID, list[OrderItem]] = defaultdict(list)
            for item_row in item_rows:
                items[item_row["order_id"]].append(_item(item_row))
            return tuple(_order(row, items[row["id"]]) for row in rows)

        return _mapped("order", build)


def _columns(table: sa.Table, *names: str) -> list[sa.ColumnElement[Any]]:
    return [table.c.id, *(table.c[name] for name in names), table.c.external_refs]


def _upsert_statement(table: sa.Table, row: dict[str, Any]) -> Any:
    statement = insert(table).values(row)
    return statement.on_conflict_do_update(
        index_elements=[table.c.id],
        set_={name: statement.excluded[name] for name in row if name != "id"},
    )


def _mapped[T](entity: str, build: Any) -> T:
    """Rebuild canonical models strictly: any invalid stored value fails closed, and
    the stored value never reaches the error."""
    try:
        return build()
    except _INVALID:
        raise CommerceStoreDataError(entity) from None


def _unavailable() -> NoReturn:
    raise CommerceStoreUnavailableError() from None


# Anything the database layer raises while reading means the store cannot answer.
_READ_FAILURES = (sa_exc.SQLAlchemyError, OSError, TimeoutError)


class _ReadSession:
    """A read session whose database failures surface only as
    ``CommerceStoreUnavailableError`` (Product-level, no driver details)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._session = sessions()

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        # Never suppresses: a failure is re-raised as the Product-level error.
        try:
            await self._session.close()
        except _READ_FAILURES:
            if exc is None:
                _unavailable()
        if isinstance(exc, _READ_FAILURES):
            _unavailable()
