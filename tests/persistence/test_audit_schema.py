"""Audit table definition: in step with the audit contract, metadata-only by design."""

import importlib.util
import re
from pathlib import Path
from typing import get_args

import sqlalchemy as sa

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


def values(enum) -> frozenset[str]:
    return frozenset(e.value for e in enum)


def parse_check(text: str) -> tuple[bool, frozenset[str]]:
    """(allows NULL, allowed values) of a ``col IN (...)`` CHECK, from SQL text.

    Works for the SQLAlchemy/migration text and for PostgreSQL's
    ``pg_get_constraintdef`` rendering (``'v'::character varying`` / ``= ANY (ARRAY[..])``).
    """
    return "IS NULL" in text.upper(), frozenset(re.findall(r"'([^']*)'", text))


def migration(path: Path = MIGRATION):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
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


class _RecordingOp:
    """Stands in for ``alembic.op``: captures what a migration's ``upgrade()`` creates."""

    def __init__(self) -> None:
        self.checks: dict[str, str] = {}

    def create_table(self, name, *elements, **kwargs) -> None:
        for element in elements:
            if isinstance(element, sa.CheckConstraint):
                self.checks[str(element.name)] = str(element.sqltext)

    def create_index(self, *args, **kwargs) -> None:
        pass


def migration_checks(module) -> dict[str, tuple[bool, frozenset[str]]]:
    """The CHECK constraints the migration's upgrade() really creates (not its source)."""
    recorder = _RecordingOp()
    module.op = recorder  # the loaded module's own reference only
    module.upgrade()
    return {name: parse_check(text) for name, text in recorder.checks.items()}


def metadata_checks(table: sa.Table) -> dict[str, tuple[bool, frozenset[str]]]:
    return {
        str(c.name): parse_check(str(c.sqltext))
        for c in table.constraints
        if isinstance(c, sa.CheckConstraint)
    }


# Frozen: the audit vocabulary migration 0002 created. Migration 0002 is a historical
# snapshot and is NEVER edited. When a new event type/status/reason is legitimately
# introduced, write a NEW migration that replaces the affected constraint(s), and move
# HEAD_AUDIT_CHECKS_MIGRATION below to it; this 0002 expectation stays as it is.
EXPECTED_0002 = {
    "ck_audit_events_event_type": (False, frozenset({
        "requested", "policy_decided", "denied", "awaiting_approval", "handler_not_registered",
        "validation_failed", "execution_started", "execution_completed", "execution_failed",
        "verification_started", "verified", "requires_human"})),
    "ck_audit_events_actor_type": (True, frozenset({"user", "api_client", "system_agent"})),
    "ck_audit_events_channel": (False, frozenset({"api", "web", "whatsapp", "system"})),
    "ck_audit_events_policy_outcome": (True, frozenset({"allow", "deny", "require_approval"})),
    "ck_audit_events_policy_reason": (True, frozenset({
        "unknown_action", "permission_denied", "read_allowed", "low_risk_write_allowed",
        "medium_risk_requires_approval", "high_risk_requires_approval"})),
    "ck_audit_events_run_status": (True, frozenset({
        "denied", "awaiting_approval", "failed", "requires_human", "verified"})),
    "ck_audit_events_run_reason": (True, frozenset({
        "policy_denied", "approval_required", "audit_unavailable", "handler_not_registered",
        "input_invalid", "handler_contract_violation", "execution_failed_no_effect",
        "execution_outcome_uncertain", "verification_failed", "verification_error",
        "audit_incomplete", "verified"})),
}  # fmt: skip
# The migration whose CHECK constraints are the ones in force at head (today: 0002).
HEAD_AUDIT_CHECKS_MIGRATION = MIGRATION


def test_migration_0002_creates_its_frozen_checks() -> None:
    m = migration()
    assert m.revision == "0002" and m.down_revision == "0001"
    assert migration_checks(m) == EXPECTED_0002


def test_metadata_has_exactly_the_seven_named_checks_of_the_head_migration() -> None:
    checks = metadata_checks(audit_events)
    assert sorted(checks) == sorted(EXPECTED_0002)  # all seven, named exactly, no others
    head = migration_checks(migration(HEAD_AUDIT_CHECKS_MIGRATION))
    # Same names, same NULL handling, same allowed vocabulary: the runtime metadata
    # mirrors the schema the head migration creates.
    assert checks == head


def test_metadata_checks_follow_the_trusted_contracts() -> None:
    checks = metadata_checks(audit_events)
    assert checks["ck_audit_events_event_type"] == (False, values(AuditEventType))
    assert checks["ck_audit_events_actor_type"] == (True, frozenset(get_args(ActorType)))
    assert checks["ck_audit_events_channel"] == (False, frozenset(get_args(Channel)))
    assert checks["ck_audit_events_policy_outcome"] == (True, values(PolicyOutcome))
    assert checks["ck_audit_events_policy_reason"] == (True, values(PolicyReason))
    assert checks["ck_audit_events_run_status"] == (True, values(ActionRunStatus))
    assert checks["ck_audit_events_run_reason"] == (True, values(ActionRunReason))


def test_parity_guard_detects_missing_or_altered_metadata_checks() -> None:
    columns = [sa.Column(c.name, c.type) for c in audit_events.columns]
    bare = sa.Table("audit_events", sa.MetaData(), *columns, schema="product")
    assert metadata_checks(bare) == {}
    assert metadata_checks(bare) != migration_checks(migration())
    widened = dict(metadata_checks(audit_events))
    nullable, allowed = widened["ck_audit_events_channel"]
    widened["ck_audit_events_channel"] = (True, allowed)
    assert widened != migration_checks(migration())


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
    # Only app.persistence defines the sink; the local/test deployment composition
    # root (Task 018) and the integration-management composition (Task 031, which audits
    # connection management through the same coordinator) are its only other users.
    # create_app and routes never see it.
    app_dir = Path(app.main.__file__).parent
    users = [p for p in app_dir.rglob("*.py") if "PostgresAuditSink" in p.read_text()]
    outside = {str(p.relative_to(app_dir)) for p in users if p.parent.name != "persistence"}
    assert outside == {"composition/local_mock.py", "composition/integrations.py"}
