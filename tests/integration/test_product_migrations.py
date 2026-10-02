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
    assert script.get_heads() == ["0005"]
    (base,) = script.get_bases()
    assert base == "0001"
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0004").down_revision == "0003"
    assert script.get_revision("0005").down_revision == "0004"
    assert [r.revision for r in script.walk_revisions()] == [
        "0005", "0004", "0003", "0002", "0001",
    ]  # fmt: skip


def alembic_config_for_scripts():
    from alembic.config import Config

    from tests.integration.product_db import ROOT

    return Config(str(ROOT / "alembic.ini"))


PRESERVED = ("write_commands", "audit_events", "integration_connections", "agent_configurations")
WORKFLOW_TABLES = {"workflow_runs", "workflow_step_runs", "workflow_events"}


def _counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(
                sa.text(f"SELECT count(*) FROM product.{table}")  # noqa: S608 - fixed names
            ).scalar_one()
            for table in PRESERVED
        }


def test_upgrade_downgrade_reupgrade_roundtrip(migrated: str, engine: sa.Engine) -> None:
    config = alembic_config(migrated)
    agno_before = agno_snapshot(engine)
    before = {"alembic_version", *PRESERVED}
    head = before | WORKFLOW_TABLES

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    with engine.begin() as connection:  # a preserved row that must survive the downgrade
        connection.execute(sa.text(
            "INSERT INTO product.agent_configurations VALUES "
            "('roundtrip-company', 'operations', false, now(), now()) ON CONFLICT DO NOTHING"
        ))  # fmt: skip
    rows_before = _counts(engine)
    assert rows_before["agent_configurations"] >= 1

    command.downgrade(config, "-1")
    # 0005 -> 0004 drops only the Workflow runtime state (tables, trigger, function): the
    # schema, its version table and every other Product table (with its rows) stay.
    assert tables(engine, "product") == before
    assert "product" in sa.inspect(engine).get_schema_names()
    assert _counts(engine) == rows_before
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0004"
        leftover = connection.execute(sa.text(
            "SELECT count(*) FROM pg_constraint WHERE conname LIKE '%workflow%'"
        )).scalar_one()  # fmt: skip
        assert leftover == 0
        functions = connection.execute(sa.text(
            "SELECT count(*) FROM pg_proc WHERE proname = 'workflow_events_append_only'"
        )).scalar_one()  # fmt: skip
        assert functions == 0

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    assert _counts(engine) == rows_before
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM product.alembic_version"))
        assert version.scalar_one() == "0005"
    with engine.begin() as connection:
        connection.execute(sa.text(
            "DELETE FROM product.agent_configurations WHERE company_id = 'roundtrip-company'"
        ))  # fmt: skip

    # Agno's schema is byte-for-byte the same shape and holds no product table.
    assert agno_snapshot(engine) == agno_before
    assert not head & set(agno_before)


# SHA-256 of migration 0001 as merged in Task 013: it must never change.
MIGRATION_0001_SHA256 = "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2"


# SHA-256 of migration 0002 as merged in Task 017: it must never change either.
MIGRATION_0002_SHA256 = "b0512d7f743451349f22f77d5b7e079e37ce49c00cba77f05cbd1c861e449ca9"


# SHA-256 of migration 0003 as merged in Task 031: it must never change either.
MIGRATION_0003_SHA256 = "e5aab3a40f51835f8a3c4606eb521fa53fc772627cd05853551fc7d72fad4ad5"


# SHA-256 of migration 0004 as merged in Task 032: it must never change either.
MIGRATION_0004_SHA256 = "22436e6a09a51a5bc031f3eece6994ed5dd9c02eb8f650731c2f254675a01786"


def test_migration_0004_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    versions = ROOT / "apps" / "api" / "migrations" / "versions"
    path = versions / "0004_create_agent_configurations.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0004_SHA256


def test_migration_0003_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    versions = ROOT / "apps" / "api" / "migrations" / "versions"
    path = versions / "0003_create_integration_connections.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0003_SHA256


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


def test_migration_0003_vocabularies_match_the_contracts() -> None:
    """0003 freezes the test-state vocabularies (it never imports app enums)."""
    import ast
    import importlib.util

    from app.integration_management import ConnectionErrorCode, ConnectionTestResult
    from tests.integration.product_db import ROOT

    versions = ROOT / "apps" / "api" / "migrations" / "versions"
    path = versions / "0003_create_integration_connections.py"
    spec = importlib.util.spec_from_file_location("migration_0003", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TEST_RESULTS == tuple(r.value for r in ConnectionTestResult)
    assert module.TEST_ERRORS == tuple(c.value for c in ConnectionErrorCode)
    imported = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    ]
    assert not [m for m in imported if (m or "").split(".")[0] == "app"]


def test_integration_connections_schema(migrated: str, engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("integration_connections",
                                                           schema="product")}  # fmt: skip
    assert set(columns) == {
        "connection_id", "company_id", "integration_id", "display_name", "config",
        "secret_fields", "enabled", "created_at", "updated_at", "last_tested_at",
        "last_test_result", "last_test_error",
    }  # fmt: skip
    # Secret VALUES have no column: only the configured field NAMES.
    for name in columns:
        assert not any(word in name for word in ("password", "token", "credential", "value"))
    assert inspector.get_pk_constraint("integration_connections", schema="product")[
        "constrained_columns"] == ["connection_id"]  # fmt: skip
    assert inspector.get_foreign_keys("integration_connections", schema="product") == []
    indexes = [
        (i["name"], i["column_names"])
        for i in inspector.get_indexes("integration_connections", schema="product")
    ]
    assert indexes == [("ix_integration_connections_company_id_created_at",
                        ["company_id", "created_at", "connection_id"])]  # fmt: skip
    checks = {c["name"] for c in inspector.get_check_constraints("integration_connections",
                                                                  schema="product")}  # fmt: skip
    assert checks == {
        "ck_integration_connections_integration_id", "ck_integration_connections_display_name",
        "ck_integration_connections_config", "ck_integration_connections_secret_fields",
        "ck_integration_connections_last_test_result",
        "ck_integration_connections_last_test_error", "ck_integration_connections_tested_at",
        "ck_integration_connections_test_error",
    }  # fmt: skip
    # No business-data (commerce mirror) table exists in the Product schema.
    assert not [t for t in inspector.get_table_names(schema="product") if "commerce" in t]


def test_migration_0005_vocabularies_match_the_contracts() -> None:
    """0005 freezes the Workflow vocabularies (it never imports app enums)."""
    import importlib.util

    from app.workflow_management.records import MAX_CHECKPOINT_BYTES, MAX_INPUT_BYTES
    from app.workflow_management.state import (
        TERMINAL_RUN_STATUSES,
        StepAttemptStatus,
        VerificationCode,
        WorkflowEventType,
        WorkflowFailureCode,
        WorkflowRunStatus,
    )
    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0005_create_workflow_runtime.py"
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RUN_STATUSES == tuple(s.value for s in WorkflowRunStatus)
    assert set(module.TERMINAL_RUN_STATUSES) == {s.value for s in TERMINAL_RUN_STATUSES}
    assert module.STEP_STATUSES == tuple(s.value for s in StepAttemptStatus)
    assert module.FAILURE_CODES == tuple(c.value for c in WorkflowFailureCode)
    assert module.EVENT_TYPES == tuple(e.value for e in WorkflowEventType)
    assert module.VERIFICATION_CODES == tuple(c.value for c in VerificationCode)
    assert (module.MAX_INPUT_BYTES, module.MAX_CHECKPOINT_BYTES) == (
        MAX_INPUT_BYTES,
        MAX_CHECKPOINT_BYTES,
    )


def test_workflow_runtime_schema(migrated: str, engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    pk = {t: inspector.get_pk_constraint(t, schema="product")["constrained_columns"]
          for t in ("workflow_runs", "workflow_step_runs", "workflow_events")}  # fmt: skip
    assert pk == {"workflow_runs": ["run_id"],
                  "workflow_step_runs": ["run_id", "step_id", "attempt"],
                  "workflow_events": ["run_id", "sequence"]}  # fmt: skip
    for table in ("workflow_step_runs", "workflow_events"):
        (fk,) = inspector.get_foreign_keys(table, schema="product")
        assert (fk["referred_table"], fk["constrained_columns"]) == ("workflow_runs", ["run_id"])
        assert not fk.get("options", {}).get("ondelete")  # never a cascading delete
    indexes = [(i["name"], i["column_names"])
               for i in inspector.get_indexes("workflow_runs", schema="product")]  # fmt: skip
    assert indexes == [("ix_workflow_runs_company_id_created_at",
                        ["company_id", "created_at", "run_id"])]  # fmt: skip
    with engine.connect() as connection:
        triggers = connection.execute(sa.text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
            "tgrelid = 'product.workflow_events'::regclass")).scalars().all()  # fmt: skip
    assert triggers == ["trg_workflow_events_append_only"]
    # No definition, Skill, Task or business-data table.
    names = set(inspector.get_table_names(schema="product"))
    assert not [
        t
        for t in names
        if any(w in t for w in ("definition", "skill", "task", "commerce", "order", "report"))
    ]
