"""Alembic owns the product schema: upgrade, downgrade, re-upgrade; Agno untouched."""

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from tests.integration.product_db import alembic_config

pytestmark = pytest.mark.integration


def tables(engine: sa.Engine, schema: str) -> set[str]:
    return set(sa.inspect(engine).get_table_names(schema=schema))


def agno_snapshot(engine: sa.Engine) -> dict[str, list]:
    inspector = sa.inspect(engine)
    if "agno_runtime" not in inspector.get_schema_names():
        return {}
    return {
        name: [(c["name"], str(c["type"])) for c in inspector.get_columns(name, "agno_runtime")]
        for name in sorted(inspector.get_table_names(schema="agno_runtime"))
    }


def test_single_linear_history_with_one_head() -> None:
    script = ScriptDirectory.from_config(alembic_config_for_scripts())
    assert script.get_heads() == ["0003"]
    (base,) = script.get_bases()
    assert base == "0001"
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0003").down_revision == "0002"
    assert [r.revision for r in script.walk_revisions()] == ["0003", "0002", "0001"]


def alembic_config_for_scripts():
    from alembic.config import Config

    from tests.integration.product_db import ROOT

    return Config(str(ROOT / "alembic.ini"))


COMMERCE_TABLES = {
    "commerce_companies", "commerce_stores", "commerce_products", "commerce_variants",
    "commerce_warehouses", "commerce_orders", "commerce_order_items", "commerce_shipments",
    "commerce_inventory_levels",
}  # fmt: skip


def test_upgrade_downgrade_reupgrade_roundtrip(migrated: str, engine: sa.Engine) -> None:
    config = alembic_config(migrated)
    agno_before = agno_snapshot(engine)
    before_commerce = {"alembic_version", "write_commands", "audit_events"}
    head = before_commerce | COMMERCE_TABLES

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    with engine.connect() as connection:
        commands_before = connection.execute(
            sa.text("SELECT count(*) FROM product.write_commands")
        ).scalar_one()
        audits_before = connection.execute(
            sa.text("SELECT count(*) FROM product.audit_events")
        ).scalar_one()

    command.downgrade(config, "-1")
    # 0003 -> 0002 drops only the Task 031 commerce tables (with their indexes and
    # constraints): the schema, its version table, write_commands and audit_events (with
    # their rows) stay.
    assert tables(engine, "product") == before_commerce
    assert "product" in sa.inspect(engine).get_schema_names()
    with engine.connect() as connection:
        assert (
            connection.execute(sa.text("SELECT count(*) FROM product.write_commands")).scalar_one()
            == commands_before
        )
        assert (
            connection.execute(sa.text("SELECT count(*) FROM product.audit_events")).scalar_one()
            == audits_before
        )
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0002"
        leftover = connection.execute(sa.text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = 'product' "
            "AND indexname LIKE '%commerce%'")).scalar_one()  # fmt: skip
        assert leftover == 0

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0003"

    # Agno's schema is byte-for-byte the same shape and holds no product table.
    assert agno_snapshot(engine) == agno_before
    assert not ({"write_commands", "audit_events"} | COMMERCE_TABLES) & set(agno_before)


# SHA-256 of migration 0001 as merged in Task 013: it must never change.
MIGRATION_0001_SHA256 = "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2"


# SHA-256 of migration 0002 as merged in Task 017: it must never change either.
MIGRATION_0002_SHA256 = "b0512d7f743451349f22f77d5b7e079e37ce49c00cba77f05cbd1c861e449ca9"


def test_migration_0002_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0002_create_audit_events.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0002_SHA256


def test_migration_0001_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0001_create_write_commands.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0001_SHA256


def test_metadata_matches_the_migrated_schema(migrated: str) -> None:
    # ``alembic check`` raises if autogenerate would emit any operation.
    command.check(alembic_config(migrated))


def test_schema_constraints(migrated: str, engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    unique = inspector.get_unique_constraints("write_commands", schema="product")
    assert [(u["name"], u["column_names"]) for u in unique] == [
        ("uq_write_commands_idempotency", ["company_id", "actor_id", "idempotency_key_hash"])
    ]
    assert inspector.get_pk_constraint("write_commands", schema="product")[
        "constrained_columns"
    ] == ["command_id"]
    # No speculative secondary indexes.
    indexes = inspector.get_indexes("write_commands", schema="product")
    assert [i for i in indexes if not i.get("duplicates_constraint")] == []
    columns = {c["name"]: c for c in inspector.get_columns("write_commands", schema="product")}
    assert set(columns) == {
        "command_id", "company_id", "actor_id", "store_id", "action_name",
        "idempotency_key_hash", "request_fingerprint", "status", "reason", "action_run_id",
        "execution_reference_id", "audit_complete", "created_at", "updated_at",
    }  # fmt: skip
    assert columns["store_id"]["nullable"] is True
    assert columns["created_at"]["type"].timezone is True
    assert columns["updated_at"]["type"].timezone is True


def test_migration_0003_vocabularies_match_the_canonical_domain() -> None:
    """0003 freezes the status vocabularies (it never imports app enums)."""
    import importlib.util

    from app.commerce.domain import OrderStatus, ProductStatus, ShipmentStatus
    from tests.integration.product_db import ROOT

    path = (
        ROOT / "apps" / "api" / "migrations" / "versions" / "0003_create_native_commerce_store.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0003", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PRODUCT_STATUSES == tuple(s.value for s in ProductStatus)
    assert module.ORDER_STATUSES == tuple(s.value for s in OrderStatus)
    assert module.SHIPMENT_STATUSES == tuple(s.value for s in ShipmentStatus)
    import ast

    imported = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ]
    assert not [m for m in imported if (m or "").split(".")[0] == "app"]


def test_commerce_schema_shape(migrated: str, engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    expected_fks = {
        "commerce_stores": {("company_id", "commerce_companies")},
        "commerce_products": {("store_id", "commerce_stores")},
        "commerce_variants": {("product_id", "commerce_products")},
        "commerce_warehouses": {("company_id", "commerce_companies")},
        "commerce_orders": {("store_id", "commerce_stores")},
        "commerce_order_items": {("order_id", "commerce_orders"),
                                 ("variant_id", "commerce_variants")},
        "commerce_shipments": {("order_id", "commerce_orders")},
        "commerce_inventory_levels": {("variant_id", "commerce_variants"),
                                      ("warehouse_id", "commerce_warehouses")},
        "commerce_companies": set(),
    }  # fmt: skip
    for table, fks in expected_fks.items():
        found = inspector.get_foreign_keys(table, schema="product")
        assert {(fk["constrained_columns"][0], fk["referred_table"]) for fk in found} == fks
        for fk in found:  # no invented deletion semantics
            options = fk.get("options") or {}
            assert fk.get("referred_schema") == "product"
            assert not options.get("ondelete") and not options.get("onupdate")
        assert inspector.get_pk_constraint(table, schema="product")["constrained_columns"] == ["id"]
    indexes = {
        table: sorted((i["name"], tuple(i["column_names"]))
                      for i in inspector.get_indexes(table, schema="product")
                      if not i.get("duplicates_constraint"))
        for table in COMMERCE_TABLES
    }  # fmt: skip
    assert {t: i for t, i in indexes.items() if i} == {
        "commerce_orders": [("ix_commerce_orders_created_at_id", ("created_at", "id")),
                            ("ix_commerce_orders_store_id_created_at_id",
                             ("store_id", "created_at", "id"))],
        "commerce_shipments": [("ix_commerce_shipments_order_id", ("order_id",)),
                               ("ix_commerce_shipments_shipped_at_id", ("shipped_at", "id"))],
        "commerce_inventory_levels": [("ix_commerce_inventory_levels_variant_id_warehouse_id",
                                       ("variant_id", "warehouse_id"))],
    }  # fmt: skip
    unique = inspector.get_unique_constraints("commerce_order_items", schema="product")
    assert [(u["name"], u["column_names"]) for u in unique] == [
        ("uq_commerce_order_items_position", ["order_id", "position"])]  # fmt: skip
    for table in COMMERCE_TABLES - {"commerce_order_items"}:
        assert inspector.get_unique_constraints(table, schema="product") == [], table
    checks = {
        table: {c["name"] for c in inspector.get_check_constraints(table, schema="product")}
        for table in COMMERCE_TABLES
    }
    assert set(checks["commerce_inventory_levels"]) == set()  # negative stock is valid
    assert set(checks["commerce_order_items"]) == {
        "ck_commerce_order_items_position",
        "ck_commerce_order_items_unit_price_currency",
    }
    assert set(checks["commerce_orders"]) == {"ck_commerce_orders_status",
                                              "ck_commerce_orders_total_currency"}  # fmt: skip
    columns = {c["name"]: c for c in inspector.get_columns("commerce_orders", schema="product")}
    for name in ("total_amount",):
        assert isinstance(columns[name]["type"], sa.Text)  # exact decimal text, no NUMERIC scale
    assert getattr(columns["created_at"]["type"], "timezone", None) is True
    assert str(columns["external_refs"]["type"]) == "JSONB"
