"""Create the Product-owned canonical commerce store (Task 031).

Nine ``product.commerce_*`` tables persisting the canonical commerce domain (company,
store, product, variant, warehouse, order, order item, shipment, inventory level).
The canonical UUID is every entity's identity. Decimal values are stored as exact
decimal TEXT (the domain imposes no precision or scale, so none is imposed here) and
external references as deterministic JSONB arrays. Explicit foreign keys, no cascading
deletion (deletion semantics are not defined). Inventory quantities may be negative
(no non-negative checks), and no (variant, warehouse) uniqueness is invented.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "product"

# Frozen snapshots of the canonical vocabularies at this revision (migrations never
# import app enums; a test keeps these in step with the current domain).
PRODUCT_STATUSES = ("active", "draft", "archived", "unknown")
ORDER_STATUSES = (
    "draft", "pending", "confirmed", "processing", "fulfilled", "cancelled", "completed",
    "unknown",
)  # fmt: skip
SHIPMENT_STATUSES = (
    "pending", "ready", "shipped", "in_transit", "delivered", "failed", "returned",
    "cancelled", "unknown",
)  # fmt: skip
CURRENCY_PATTERN = "^[A-Z]{3}$"

# Creation order (parents first); downgrade drops in reverse.
TABLES = (
    "commerce_companies", "commerce_stores", "commerce_products", "commerce_variants",
    "commerce_warehouses", "commerce_orders", "commerce_order_items", "commerce_shipments",
    "commerce_inventory_levels",
)  # fmt: skip
INDEXES = (
    ("ix_commerce_orders_created_at_id", "commerce_orders", ["created_at", "id"]),
    ("ix_commerce_orders_store_id_created_at_id", "commerce_orders",
     ["store_id", "created_at", "id"]),
    ("ix_commerce_shipments_order_id", "commerce_shipments", ["order_id"]),
    ("ix_commerce_shipments_shipped_at_id", "commerce_shipments", ["shipped_at", "id"]),
    ("ix_commerce_inventory_levels_variant_id_warehouse_id", "commerce_inventory_levels",
     ["variant_id", "warehouse_id"]),
)  # fmt: skip


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _currency(column: str) -> str:
    return f"{column} ~ '{CURRENCY_PATTERN}'"


def _refs() -> sa.Column:
    return sa.Column("external_refs", postgresql.JSONB(), nullable=False)


def _fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{SCHEMA}.{parent}.id"], name=f"fk_{table}_{column}")


def upgrade() -> None:
    op.create_table(
        "commerce_companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        _refs(),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_companies"),
        schema=SCHEMA,
    )
    op.create_table(
        "commerce_stores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        _refs(),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_stores"),
        _fk("commerce_stores", "company_id", "commerce_companies"),
        sa.CheckConstraint(_currency("currency"), name="ck_commerce_stores_currency"),
        schema=SCHEMA,
    )
    op.create_table(
        "commerce_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_status", sa.Text(), nullable=True),
        _refs(),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_products"),
        _fk("commerce_products", "store_id", "commerce_stores"),
        sa.CheckConstraint(_in("status", PRODUCT_STATUSES), name="ck_commerce_products_status"),
        schema=SCHEMA,
    )
    op.create_table(
        "commerce_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=True),
        _refs(),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_variants"),
        _fk("commerce_variants", "product_id", "commerce_products"),
        schema=SCHEMA,
    )
    op.create_table(
        "commerce_warehouses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        _refs(),
        sa.PrimaryKeyConstraint("id", name="pk_commerce_warehouses"),
        _fk("commerce_warehouses", "company_id", "commerce_companies"),
        schema=SCHEMA,
    )
    op.create_table(
        "commerce_orders",
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
        sa.CheckConstraint(_in("status", ORDER_STATUSES), name="ck_commerce_orders_status"),
        sa.CheckConstraint(_currency("total_currency"),
                           name="ck_commerce_orders_total_currency"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_table(
        "commerce_order_items",
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
        sa.CheckConstraint(_currency("unit_price_currency"),
                           name="ck_commerce_order_items_unit_price_currency"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_table(
        "commerce_shipments",
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
        sa.CheckConstraint(_in("status", SHIPMENT_STATUSES),
                           name="ck_commerce_shipments_status"),
        schema=SCHEMA,
    )  # fmt: skip
    op.create_table(
        "commerce_inventory_levels",
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
        schema=SCHEMA,
    )
    # Only what the commerce integration contract's reads need.
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, schema=SCHEMA)


def downgrade() -> None:
    # Only the Task 031 tables (and their indexes/constraints), children first: never
    # the schema, write_commands, audit_events, or a cascading drop.
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table, schema=SCHEMA)
    for table in reversed(TABLES):
        op.drop_table(table, schema=SCHEMA)
