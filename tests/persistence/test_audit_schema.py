"""Audit table definition: in step with the audit contract, metadata-only by design."""

import importlib.util
from pathlib import Path

from app.context.models import ActorType, Channel
from app.execution import ActionRunReason, ActionRunStatus, AuditEvent, AuditEventType
from app.governance import PolicyOutcome, PolicyReason
from app.persistence import audit_events

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps" / "api" / "migrations" / "versions" / "0002_create_audit_events.py"
RAW_COLUMN_WORDS = (
    "parameter", "input", "payload", "body", "prompt", "message", "provider", "response",
    "raw", "result", "exception", "error", "api_key", "idempotency", "fingerprint",
    "header", "authorization", "secret", "token", "password", "title", "description",
)  # fmt: skip


def migration():
    spec = importlib.util.spec_from_file_location("migration_0002", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_table_columns_are_exactly_the_audit_event_plus_recorded_at() -> None:
    assert set(audit_events.c.keys()) == set(AuditEvent.model_fields) | {"recorded_at"}
    assert [c.name for c in audit_events.primary_key.columns] == ["event_id"]
    assert audit_events.schema == "product"


def test_no_raw_data_columns() -> None:
    offenders = [c for c in audit_events.c.keys() if any(w in c for w in RAW_COLUMN_WORDS)]
    assert offenders == []


def test_nullability_matches_the_event_contract() -> None:
    required = {"event_id", "run_id", "request_id", "occurred_at", "event_type",
                "action_name", "company_id", "channel", "recorded_at"}  # fmt: skip
    assert {c.name for c in audit_events.c if not c.nullable} == required


def test_migration_vocabulary_matches_the_current_contracts() -> None:
    m = migration()
    assert m.revision == "0002" and m.down_revision == "0001"
    assert set(m.EVENT_TYPES) == {e.value for e in AuditEventType}
    assert set(m.ACTOR_TYPES) == set(ActorType.__args__)
    assert set(m.CHANNELS) == set(Channel.__args__)
    assert set(m.POLICY_OUTCOMES) == {e.value for e in PolicyOutcome}
    assert set(m.POLICY_REASONS) == {e.value for e in PolicyReason}
    assert set(m.RUN_STATUSES) == {e.value for e in ActionRunStatus}
    assert set(m.RUN_REASONS) == {e.value for e in ActionRunReason}


def test_downgrade_touches_only_audit_events() -> None:
    source = MIGRATION.read_text()
    import ast

    func = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == "downgrade")  # fmt: skip
    downgrade = ast.unparse([n for n in func.body if not isinstance(n, ast.Expr)])
    assert "write_commands" not in downgrade
    assert "drop_schema" not in downgrade.lower() and "cascade" not in downgrade.lower()
    code = ast.unparse(ast.parse(source))  # without comments
    assert "ForeignKey" not in code and "references" not in code.lower()


def test_sink_is_append_only_and_has_no_hidden_state() -> None:
    import ast
    import inspect

    from app.persistence import audit

    source = inspect.getsource(audit)
    tree = ast.parse(source)
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "sa.insert" in calls
    for forbidden in ("sa.update", "sa.delete", "on_conflict_do_nothing",
                      "on_conflict_do_update", "create_all", "asyncio.sleep"):  # fmt: skip
        assert forbidden not in calls and forbidden not in source
    module_level = [n for n in tree.body if isinstance(n, ast.Assign)]
    assert all("engine" not in ast.unparse(n).lower() for n in module_level)
    record = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "record")  # fmt: skip
    record_src = ast.unparse(record)
    assert "RequestContext" not in record_src and "ActionRun" not in record_src


def test_execution_stays_persistence_independent() -> None:
    import app.execution

    for path in Path(app.execution.__file__).parent.rglob("*.py"):
        text = path.read_text()
        assert "sqlalchemy" not in text and "app.persistence" not in text, path.name


def test_audit_sink_is_not_wired_and_has_no_http_surface() -> None:
    import app.main
    import app.routes

    main = Path(app.main.__file__).read_text()
    assert "PostgresAuditSink" not in main and "audit_events" not in main
    for path in Path(app.routes.__file__).parent.rglob("*.py"):
        text = path.read_text()
        assert "PostgresAuditSink" not in text and "audit_events" not in text, path.name
        assert "/audit" not in text, path.name
    # Only migrations/env.py (metadata) and app.persistence reference the table.
    users = [p for p in Path(app.main.__file__).parent.rglob("*.py")
             if "PostgresAuditSink" in p.read_text()]  # fmt: skip
    assert {p.parent.name for p in users} == {"persistence"}
