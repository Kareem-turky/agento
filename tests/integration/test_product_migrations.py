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
    assert script.get_heads() == ["0007"]
    (base,) = script.get_bases()
    assert base == "0001"
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0004").down_revision == "0003"
    assert script.get_revision("0005").down_revision == "0004"
    assert script.get_revision("0006").down_revision == "0005"
    assert script.get_revision("0007").down_revision == "0006"
    assert [r.revision for r in script.walk_revisions()] == [
        "0007", "0006", "0005", "0004", "0003", "0002", "0001",
    ]  # fmt: skip


def alembic_config_for_scripts():
    from alembic.config import Config

    from tests.integration.product_db import ROOT

    return Config(str(ROOT / "alembic.ini"))


PRESERVED = ("write_commands", "audit_events", "integration_connections", "agent_configurations")
WORKFLOW_TABLES = {"workflow_runs", "workflow_step_runs", "workflow_events"}
KNOWLEDGE_TABLES = {
    "company_operating_model_versions", "company_operating_model_current",
    "knowledge_documents", "knowledge_document_versions", "knowledge_chunks",
}  # fmt: skip
APPROVAL_TABLES = {"approval_requests", "approval_events"}
# Task 036 adds a nullable approval correlation column to these existing tables.
APPROVAL_CORRELATED = ("write_commands", "audit_events", "workflow_step_runs")


_PRIOR_TABLES = (*PRESERVED, *sorted(WORKFLOW_TABLES))


def _counts(engine: sa.Engine, names: tuple[str, ...] = _PRIOR_TABLES) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(
                sa.text(f"SELECT count(*) FROM product.{table}")  # noqa: S608 - fixed names
            ).scalar_one()
            for table in names
        }


def _version(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM product.alembic_version")
        ).scalar_one()


def _leftovers(engine: sa.Engine, pattern: str, function: str) -> tuple[int, int]:
    with engine.connect() as connection:
        constraints = connection.execute(
            sa.text(
                "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n "
                "ON n.oid = c.connamespace WHERE n.nspname = 'product' AND c.conname LIKE :p"
            ),
            {"p": pattern},
        ).scalar_one()
        functions = connection.execute(
            sa.text("SELECT count(*) FROM pg_proc WHERE proname = :f"), {"f": function}
        ).scalar_one()
    return constraints, functions


def test_upgrade_downgrade_reupgrade_roundtrip(migrated: str, engine: sa.Engine) -> None:
    config = alembic_config(migrated)
    agno_before = agno_snapshot(engine)
    before_workflows = {"alembic_version", *PRESERVED}
    before_knowledge = before_workflows | WORKFLOW_TABLES  # the prior 7 Product tables
    before_approvals = before_knowledge | KNOWLEDGE_TABLES
    head = before_approvals | APPROVAL_TABLES

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    with engine.begin() as connection:  # preserved rows that must survive the downgrades
        connection.execute(sa.text(
            "INSERT INTO product.agent_configurations VALUES "
            "('roundtrip-company', 'operations', false, now(), now()) ON CONFLICT DO NOTHING"
        ))  # fmt: skip
        connection.execute(sa.text(
            "INSERT INTO product.workflow_runs (run_id, workflow_id, workflow_version, "
            "request_id, company_id, actor_id, actor_type, channel, store_id, status, "
            "current_step_id, failure_code, input_state, input_fingerprint, lease_owner, "
            "lease_expires_at, created_at, updated_at, completed_at) VALUES "
            "('00000000-0000-4000-8000-0000000000aa', 'operations.daily_report', 1, "
            "'00000000-0000-4000-8000-0000000000ab', 'roundtrip-company', 'roundtrip-actor', "
            "'user', 'api', NULL, 'pending', NULL, NULL, '{}'::jsonb, "
            "repeat('a', 64), NULL, NULL, now(), now(), NULL) ON CONFLICT DO NOTHING"
        ))  # fmt: skip
        connection.execute(sa.text(
            "INSERT INTO product.knowledge_documents VALUES ('00000000-0000-4000-8000-"
            "0000000000ac', 'roundtrip-company', 'general', 'active', 1, now(), now())"
        ))  # fmt: skip
        connection.execute(sa.text(
            "INSERT INTO product.knowledge_document_versions VALUES ('00000000-0000-4000-8000-"
            "0000000000ac', 1, 'roundtrip-company', 'T', 'text/plain', 'B', repeat('b', 64), "
            "'roundtrip-actor', now())"
        ))  # fmt: skip
        # An audit row written under the Task 036 vocabulary (kept by the downgrade).
        connection.execute(sa.text(
            "INSERT INTO product.audit_events (event_id, run_id, request_id, occurred_at, "
            "event_type, action_name, company_id, channel, run_status, run_reason, "
            "approval_id) VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
            "now(), 'approval_refused', 'test.budget.update', 'roundtrip-company', 'api', "
            "'failed', 'approval_unavailable', gen_random_uuid())"
        ))  # fmt: skip
    rows_before = _counts(engine)
    assert rows_before["agent_configurations"] >= 1 and rows_before["workflow_runs"] >= 1
    columns = {t: {c["name"] for c in sa.inspect(engine).get_columns(t, schema="product")}
               for t in APPROVAL_CORRELATED}  # fmt: skip
    assert all("approval_id" in c for c in columns.values())

    command.downgrade(config, "-1")
    # 0007 -> 0006 drops only the Task 036 schema (approval tables, trigger function and
    # correlation columns). Every prior row stays, including audit history written under
    # the newer vocabulary: the narrower CHECK is restored NOT VALID instead.
    assert tables(engine, "product") == before_approvals
    assert _counts(engine) == rows_before
    assert _version(engine) == "0006"
    assert _leftovers(engine, "%approval%", "approval_events_append_only") == (0, 0)
    for table in APPROVAL_CORRELATED:
        names = {c["name"] for c in sa.inspect(engine).get_columns(table, schema="product")}
        assert "approval_id" not in names, table
    with engine.connect() as connection:
        kept = connection.execute(sa.text(
            "SELECT count(*) FROM product.audit_events WHERE event_type = 'approval_refused'"
        )).scalar_one()  # fmt: skip
        validated = dict(connection.execute(sa.text(
            "SELECT conname, convalidated FROM pg_constraint WHERE conname IN "
            "('ck_audit_events_event_type', 'ck_audit_events_run_reason')")).all())  # fmt: skip
    assert kept >= 1 and validated == {"ck_audit_events_event_type": False,
                                       "ck_audit_events_run_reason": False}  # fmt: skip

    command.downgrade(config, "-1")
    # 0006 -> 0005 drops only the Knowledge tables (with their trigger function): the
    # schema, its version table and the prior 7 Product tables (with their rows) stay.
    assert tables(engine, "product") == before_knowledge
    assert _counts(engine) == rows_before
    assert _version(engine) == "0005"
    assert _leftovers(engine, "%knowledge%", "knowledge_reject_mutation") == (0, 0)
    assert _leftovers(engine, "%operating_model%", "knowledge_reject_mutation") == (0, 0)

    command.downgrade(config, "-1")
    # 0005 -> 0004 drops only the Workflow runtime state (tables, trigger, function).
    assert tables(engine, "product") == before_workflows
    assert "product" in sa.inspect(engine).get_schema_names()
    assert _counts(engine, PRESERVED) == {t: rows_before[t] for t in PRESERVED}
    assert _version(engine) == "0004"
    assert _leftovers(engine, "%workflow%", "workflow_events_append_only") == (0, 0)

    command.upgrade(config, "head")
    assert tables(engine, "product") == head
    assert _counts(engine, PRESERVED) == {t: rows_before[t] for t in PRESERVED}
    assert _version(engine) == "0007"
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


# SHA-256 of migration 0005 as merged in Task 034: it must never change either.
MIGRATION_0005_SHA256 = "3984d4a3f3a3f9318511489da49fd0035b4d7246065b05e940f5223292bee8dc"


def test_migration_0005_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0005_create_workflow_runtime.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0005_SHA256


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
        "approval_id",  # Task 036: correlation only (never parameters)
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
    # 0005 stays frozen; Task 036 (0007) appends ``workflow_approval_resumed``.
    assert module.EVENT_TYPES == tuple(
        e.value for e in WorkflowEventType if e is not WorkflowEventType.WORKFLOW_APPROVAL_RESUMED
    )
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


# SHA-256 of migration 0006 as merged in Task 035: it must never change either.
MIGRATION_0006_SHA256 = "ca308ddd3f2f63f8f3b171a5643da2d6832da7c4c712c2f3338174cc53a59b2a"


def test_migration_0006_is_byte_for_byte_unchanged() -> None:
    import hashlib

    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0006_create_knowledge_context.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_0006_SHA256


def test_migration_0007_vocabularies_match_the_contracts() -> None:
    """0007 freezes the approval vocabularies (it never imports app enums)."""
    import importlib.util

    from app.approval_management.models import MAX_NOTE_CHARS
    from app.approval_management.state import ApprovalEventType, ApprovalStatus
    from app.execution import ActionRunReason, ApprovalOutcome, ApprovalSource, AuditEventType
    from app.workflow_management.state import WorkflowEventType
    from tests.integration.product_db import ROOT

    path = ROOT / "apps" / "api" / "migrations" / "versions" / "0007_create_approvals.py"
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0007", "0006")
    assert module.STATUSES == tuple(s.value for s in ApprovalStatus)
    assert module.EVENT_TYPES == tuple(e.value for e in ApprovalEventType)
    assert module.SOURCES == tuple(s.value for s in ApprovalSource)
    assert module.OUTCOMES == tuple(o.value for o in ApprovalOutcome)
    assert module.RISKS == ("medium_risk", "high_risk")
    assert module.MAX_NOTE_CHARS == MAX_NOTE_CHARS
    assert set(module.AUDIT_EVENT_TYPES) == {e.value for e in AuditEventType}
    assert set(module.RUN_REASONS) == {r.value for r in ActionRunReason}
    assert module.WORKFLOW_EVENT_TYPES == tuple(e.value for e in WorkflowEventType)


def test_approval_schema(migrated: str, engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("approval_requests", schema="product")}
    assert {"subject_fingerprint", "summary", "status", "expires_at", "consumed_at"} <= columns
    for column in columns:
        for word in ("parameter", "payload", "input", "raw", "body", "prompt", "secret",
                     "token", "password", "credential", "idempotency"):  # fmt: skip
            assert word not in column, column
    assert inspector.get_pk_constraint("approval_events", schema="product")[
        "constrained_columns"
    ] == ["approval_id", "sequence"]
    triggers = {r[0] for r in engine.connect().execute(sa.text(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
        "tgrelid = 'product.approval_events'::regclass"))}  # fmt: skip
    assert triggers == {"approval_events_append_only"}
