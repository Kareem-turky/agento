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
    assert script.get_heads() == ["0002"]
    (base,) = script.get_bases()
    assert base == "0001"
    assert script.get_revision("0002").down_revision == "0001"


def alembic_config_for_scripts():
    from alembic.config import Config

    from tests.integration.product_db import ROOT

    return Config(str(ROOT / "alembic.ini"))


def test_upgrade_downgrade_reupgrade_roundtrip(migrated: str, engine: sa.Engine) -> None:
    config = alembic_config(migrated)
    agno_before = agno_snapshot(engine)
    both = {"alembic_version", "write_commands", "audit_events"}

    command.upgrade(config, "head")
    assert tables(engine, "product") == both
    with engine.connect() as connection:
        commands_before = connection.execute(
            sa.text("SELECT count(*) FROM product.write_commands")
        ).scalar_one()

    command.downgrade(config, "-1")
    # 0002 -> 0001 drops only audit_events: the schema, its version table and
    # write_commands (with its rows) stay.
    assert tables(engine, "product") == {"alembic_version", "write_commands"}
    assert "product" in sa.inspect(engine).get_schema_names()
    with engine.connect() as connection:
        assert (
            connection.execute(sa.text("SELECT count(*) FROM product.write_commands")).scalar_one()
            == commands_before
        )
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0001"

    command.upgrade(config, "head")
    assert tables(engine, "product") == both
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0002"

    # Agno's schema is byte-for-byte the same shape and holds no product table.
    assert agno_snapshot(engine) == agno_before
    assert "write_commands" not in agno_before and "audit_events" not in agno_before


# SHA-256 of migration 0001 as merged in Task 013: it must never change.
MIGRATION_0001_SHA256 = "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2"


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
