"""Task 039 architecture guards: system operations observe the Product, never change it."""

import ast
import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.system_operations import (
    EXPECTED_PRODUCT_SCHEMA_REVISION,
    SYSTEM_ACTIONS,
    SYSTEM_READ,
    SYSTEM_READ_PERMISSION,
)

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "api" / "app"
SYSTEM = APP / "system_operations"
MIGRATIONS = ROOT / "apps" / "api" / "migrations"

# Task 039 owns no schema: every Product migration stays byte-identical (Task 042 adds
# 0009, pinned here too).
MIGRATION_SHA256 = {
    "0001": "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2",
    "0002": "b0512d7f743451349f22f77d5b7e079e37ce49c00cba77f05cbd1c861e449ca9",
    "0003": "e5aab3a40f51835f8a3c4606eb521fa53fc772627cd05853551fc7d72fad4ad5",
    "0004": "22436e6a09a51a5bc031f3eece6994ed5dd9c02eb8f650731c2f254675a01786",
    "0005": "3984d4a3f3a3f9318511489da49fd0035b4d7246065b05e940f5223292bee8dc",
    "0006": "ca308ddd3f2f63f8f3b171a5643da2d6832da7c4c712c2f3338174cc53a59b2a",
    "0007": "0bab564c73a9bcc9a1e24568c1627e707b813f37fcd172d41a48df26b280fde4",
    "0008": "baf48dc6ba3b47d75fb65b9da0ccc3c3b15a7a66d1d71d9490155c8a2aa04ab2",
    "0009": "2c56128f144c3d470d563a055b7fcf215eec47e09d7407e6275076f20bc541f6",
}


def modules(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ----- schema ------------------------------------------------------------------------------------


def test_expected_revision_is_the_single_alembic_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == [EXPECTED_PRODUCT_SCHEMA_REVISION] == ["0009"]


def test_migrations_are_unchanged() -> None:
    files = sorted((MIGRATIONS / "versions").glob("*.py"))
    assert [p.name[:4] for p in files] == sorted(MIGRATION_SHA256)
    for path in files:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == MIGRATION_SHA256[path.name[:4]]


def test_the_revision_constant_is_stated_once() -> None:
    holders = [
        str(p.relative_to(APP))
        for p in modules(APP)
        if '"0009"' in p.read_text() or "'0009'" in p.read_text()
    ]
    assert holders == ["system_operations/revision.py"]
    # No runtime code imports an Alembic migration module.
    for path in modules(APP):
        assert not any(n.startswith(("alembic", "migrations")) for n in imported(path)), path


# ----- boundaries --------------------------------------------------------------------------------


def test_system_operations_core_has_no_framework_storage_model_or_integration() -> None:
    forbidden = (
        "fastapi",
        "starlette",
        "sqlalchemy",
        "psycopg",
        "agno",
        "httpx",
        "socket",
        "redis",
        "opentelemetry",
        "app.persistence",
        "app.agents",
        "app.runtime",
        "app.integrations",
        "app.integration_management",
        "app.composition",
        "app.operations",
        "app.workflow_management",
        "app.knowledge",
        "app.approval_management",
        "app.conversations",
        "app.commands",
        "app.execution",
        "app.routes",
    )
    for path in modules(SYSTEM):
        for name in imported(path):
            assert not name.startswith(forbidden), (path.name, name)


def test_readiness_probe_is_read_only_and_bounded() -> None:
    probe = (APP / "persistence" / "system_readiness.py").read_text()
    assert "asyncio.timeout(self._timeout)" in probe and "DEFAULT_TIMEOUT_SECONDS = 2.0" in probe
    assert "NullPool" in probe and '"connect_timeout"' in probe
    upper = probe.upper()
    for write in (
        "INSERT",
        "UPDATE ",
        "DELETE",
        "CREATE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE",
        "UPGRADE",
        "COMMIT",
    ):
        assert write not in upper, write
    assert "redis" not in probe.lower()
    # One attempt per call: no retry loop.
    assert "for " not in probe.split("async def check", 1)[1].split("schema =", 1)[0]
    assert "while " not in probe


def test_system_read_is_the_only_system_permission() -> None:
    assert SYSTEM_ACTIONS == (SYSTEM_READ,)
    assert SYSTEM_READ.name == SYSTEM_READ_PERMISSION == "system.read"
    assert SYSTEM_READ.risk.value == "read"
    for path in modules(APP):
        source = path.read_text()
        for word in (
            "system.write",
            "system.restart",
            "system.backup",
            "system.restore",
            "system.manage",
        ):
            assert word not in source, (path, word)


def test_system_routes_are_read_only_and_public_health_is_minimal() -> None:
    routes = (APP / "routes" / "system.py").read_text()
    tree = ast.parse(routes)
    decorators = [
        ast.unparse(d.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        for d in node.decorator_list
        if isinstance(d, ast.Call)
    ]
    assert decorators == ["router.get", "router.get", "router.get"]
    code = "\n".join(line.split("#", 1)[0] for line in routes.splitlines())
    live = code.split("async def live", 1)[1].split("\n\n\n", 1)[0]
    ready = code.split("async def ready", 1)[1].split("\n\n\n", 1)[0]
    assert 'JSONResponse({"status": "alive"}, headers=_NO_STORE)' in live
    for body in (live, ready):
        for word in ("settings", "version", "environment", "reason", "components", "detail"):
            assert word not in body, word


def test_system_operations_never_touch_business_data_or_agents() -> None:
    for path in [
        *modules(SYSTEM),
        APP / "routes" / "system.py",
        APP / "persistence" / "system_readiness.py",
        APP / "composition" / "system.py",
    ]:
        source = path.read_text()
        for word in (
            "run_operations",
            "arun(",
            ".run(",
            "default_model",
            "approve",
            "consume",
            "resume",
            "message.text",
            "knowledge_chunks",
            "document_versions",
            "test_connection",
            "create_ticket",
            "set_enabled",
        ):
            assert word not in source, (path.name, word)


def test_one_product_observability_and_telemetry_stops_last() -> None:
    bootstrap = (APP / "bootstrap.py").read_text()
    assert bootstrap.count("build_deployment_observability(settings)") == 1
    close = bootstrap.split("async def close()", 1)[1].split("\n    try:\n        return", 1)[0]
    assert close.rstrip().endswith("stop_telemetry()")
    assert "[stop_telemetry, composition.discard]" in bootstrap  # discarded last as well
    deployment = (APP / "observability" / "deployment.py").read_text()
    assert "set_tracer_provider" not in deployment and "set_meter_provider" not in deployment
    assert "shutdown_on_exit=False" in deployment
    assert '"service.name": SERVICE_NAME' in deployment and 'SERVICE_NAME = "agento"' in deployment
    resource = deployment.split("Resource(", 1)[1].split(")", 1)[0]
    assert sorted(
        line.split(":")[0].strip().strip('"') for line in resource.splitlines() if ":" in line
    ) == ["deployment.environment.name", "service.name", "service.version"]
    assert "Resource.create" not in deployment  # no detectors, no OTEL_RESOURCE_ATTRIBUTES
    assert "headers=" not in deployment  # no exporter header or credential


def test_catalogs_registries_and_ticket_risk_are_unchanged() -> None:
    from app.agent_management.catalog import build_default_agent_catalog
    from app.agent_management.skills import build_default_skill_catalog
    from app.agent_management.tasks import build_default_task_catalog
    from app.composition.registry import build_default_backend_registry
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.operations import OPERATIONS_ACTIONS
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})
    assert {s.skill_id for s in build_default_skill_catalog().definitions()} == {
        "operations.order_inspection",
        "operations.daily_analysis",
        "operations.ticket_escalation",
    }
    assert {t.task_id for t in build_default_task_catalog().definitions()} == {
        "operations.inspect_order",
        "operations.analyze_daily",
        "operations.escalate_issue",
    }
    assert build_default_workflow_catalog().workflow_ids == frozenset({"operations.daily_report"})
    catalog = build_default_integration_catalog()
    assert len(catalog) == 0 and len(build_default_messaging_registry(catalog)) == 0
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})
    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk.value == "low_risk_write"


def test_agno_pin_and_no_hosted_dependency() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '"agno[os,postgres,openai,anthropic]==3.0.11"' in pyproject
    lock = (ROOT / "uv.lock").read_text()
    for hosted in ("agno-cloud", "agno_cloud", "datadog", "sentry-sdk", "newrelic"):
        assert hosted not in lock, hosted


# ----- bootstrap lifecycle ------------------------------------------------------------------------


def test_deployment_app_stops_telemetry_once_on_shutdown(monkeypatch, settings) -> None:
    from fastapi.testclient import TestClient

    import app.bootstrap as bootstrap
    from app.observability import ObservabilityRuntime, OpenTelemetryObservability

    stops: list[str] = []
    runtime = ObservabilityRuntime(
        OpenTelemetryObservability(), "otlp_http", (lambda: stops.append("telemetry"),)
    )
    monkeypatch.setattr(bootstrap, "build_deployment_observability", lambda _settings: runtime)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    from agno.os.settings import AgnoAPISettings

    from tests.conftest import TEST_OS_SECURITY_KEY

    app = bootstrap.create_deployment_app(
        settings, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY)
    )
    assert app.state.system_operations_service is not None
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert stops == []
    assert stops == ["telemetry"]
