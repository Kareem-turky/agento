"""Observability boundaries: OpenTelemetry isolation, provider/backend independence,
no new routes or agent tools. Task 039 (reviewed): OPTIONAL OTLP/HTTP export, disabled by
default, confined to ``app.observability.deployment`` and two startup settings."""

import ast
import inspect
import re
import tomllib
from pathlib import Path

import pytest

from app.bootstrap import create_deployment_app
from app.config import Settings
from app.main import create_app
from app.runtime import RuntimeConfigurationError
from tests.routes import effective_api_routes
from tests.support.observability import RecordingObservability

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "api" / "app"
OBSERVABILITY = APP / "observability"
# Task 039: the ONLY module that may touch the OpenTelemetry SDK/exporter and threads.
DEPLOYMENT = OBSERVABILITY / "deployment.py"


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


def test_only_the_observability_package_imports_opentelemetry() -> None:
    offenders = [
        str(path.relative_to(APP)) for path in modules(APP)
        if OBSERVABILITY not in path.parents
        and any(name.split(".")[0] == "opentelemetry" for name in imported(path))
    ]  # fmt: skip
    assert offenders == []
    # Inside the package only the implementation module touches OpenTelemetry.
    users = {p.name for p in modules(OBSERVABILITY)
             if any(n.split(".")[0] == "opentelemetry" for n in imported(p))}  # fmt: skip
    assert users == {"otel.py", "deployment.py"}
    # The SDK and the exporter are imported only by the deployment runtime, and only
    # lazily inside the enabled branch (disabled export never imports them).
    sdk = {p.name for p in modules(OBSERVABILITY)
           if any(n.startswith(("opentelemetry.sdk", "opentelemetry.exporter"))
                  for n in imported(p))}  # fmt: skip
    assert sdk == {"deployment.py"}
    top_level = {
        node.module for node in ast.parse(DEPLOYMENT.read_text()).body
        if isinstance(node, ast.ImportFrom) and node.module
    }  # fmt: skip
    assert not [m for m in top_level if m.startswith("opentelemetry")]


@pytest.mark.parametrize(
    "layer",
    ["routes", "services", "agents", "workflows", "governance", "execution", "commands",
     "commerce", "integrations", "application", "persistence", "operations", "company",
     "auth", "context", "runtime", "composition"],
)  # fmt: skip
def test_business_layers_do_not_depend_on_observability(layer: str) -> None:
    for path in modules(APP / layer):
        names = imported(path)
        if layer == "composition":
            # Task 034: the composition boundary CARRIES the application's one Product
            # observability (chosen by app.bootstrap) to the services it composes. It may
            # name only the contract, never the implementation, and never build one.
            names = names - {"app.observability.contracts"}
            source = path.read_text()
            for forbidden in ("build_default_observability", "OpenTelemetryObservability"):
                assert forbidden not in source, (path, forbidden)
        assert not any(n.startswith("app.observability") for n in names), path


def test_one_product_observability_per_application() -> None:
    """Task 034: app.bootstrap chooses the observability ONCE and hands the same instance
    to the business composition and to create_app; the Workflow engine never builds one."""
    bootstrap = (APP / "bootstrap.py").read_text()
    # Task 039: the deployment observability runtime replaces the bare default, once.
    assert "build_default_observability" not in bootstrap
    assert bootstrap.count("build_deployment_observability(settings)") == 1
    assert "build_deployment_composition(settings, model=model, observability=observer)" in (
        bootstrap
    )
    assert "observability=observer," in bootstrap
    engine = APP / "workflow_management" / "engine.py"
    assert "build_default_observability" not in engine.read_text()
    assert "app.observability" not in imported(engine)  # the contracts module only
    assert "app.observability.contracts" in imported(engine)


def test_observability_depends_on_no_backend_provider_model_or_storage() -> None:
    forbidden_prefixes = (
        "agno", "app.agents", "app.composition", "app.integrations", "app.persistence",
        "app.commands", "app.execution", "app.workflows", "app.runtime", "sqlalchemy",
        "psycopg", "redis", "httpx", "requests", "urllib", "aiohttp", "socket", "grpc",
        "threading", "asyncio", "opentelemetry.sdk", "opentelemetry.exporter",
    )  # fmt: skip
    # Task 039: the deployment runtime alone may use a thread (bounded shutdown) and the
    # OpenTelemetry SDK / OTLP-HTTP exporter (never another exporter or transport).
    allowed_for_deployment = ("threading", "opentelemetry.sdk",
                              "opentelemetry.exporter.otlp.proto.http")  # fmt: skip
    for path in modules(OBSERVABILITY):
        for name in imported(path):
            if path == DEPLOYMENT and name.startswith(allowed_for_deployment):
                continue
            assert not name.startswith(forbidden_prefixes), (path.name, name)


def test_observability_code_names_no_provider_backend_or_exporter() -> None:
    words = re.compile(
        r"mock|backend_id|business_backend|registry|exporter|otlp|prometheus|jaeger|zipkin|"
        r"datadog|new_relic|newrelic|grafana|sentry|collector|endpoint|api_key|input_tokens|output_tokens|"
        r"total_tokens|usage|\bcost\b|shopify|woocommerce|salla|zid",
        re.IGNORECASE,
    )
    # Task 039: the deployment runtime names its OTLP exporter, collector and endpoint.
    export_words = {"exporter", "otlp", "collector", "endpoint"}
    for path in modules(OBSERVABILITY):
        code = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )  # fmt: skip
        tree = ast.parse(code)
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for docstring in filter(None, docstrings):
            code = code.replace(docstring, "")
        found = [m.group(0).lower() for m in words.finditer(code)]
        if path == DEPLOYMENT:
            found = [word for word in found if word not in export_words]
        assert found == [], (path.name, found)


def test_no_exporter_or_vendor_dependency_is_declared() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = pyproject["project"]["dependencies"] + pyproject["dependency-groups"]["dev"]
    otel = sorted(d for d in declared if d.startswith("opentelemetry"))
    # Task 039: the SDK is a runtime dependency and ONE vendor-neutral OTLP/HTTP exporter
    # is added, all aligned with the API version.
    assert otel == ["opentelemetry-api==1.45.0",
                    "opentelemetry-exporter-otlp-proto-http==1.45.0",
                    "opentelemetry-sdk==1.45.0"]  # fmt: skip
    assert otel == sorted(d for d in pyproject["project"]["dependencies"]
                          if d.startswith("opentelemetry"))  # fmt: skip
    lock = (ROOT / "uv.lock").read_text()
    exporters = sorted(re.findall(r'^name = "(opentelemetry-exporter[^"]*)"', lock, re.MULTILINE))
    assert exporters == ["opentelemetry-exporter-http-transport",
                         "opentelemetry-exporter-otlp-common",
                         "opentelemetry-exporter-otlp-proto-common",
                         "opentelemetry-exporter-otlp-proto-http"]  # fmt: skip
    for vendor in ("opentelemetry-exporter-otlp-proto-grpc", "grpcio", "prometheus",
                   "jaeger", "zipkin", "datadog", "ddtrace", "newrelic", "sentry"):  # fmt: skip
        assert f'name = "{vendor}' not in lock, vendor


def test_no_observability_credentials_exist() -> None:
    # Task 039: exactly two startup settings (mode, collector base URL); no exporter
    # header, token, key or credential setting of any kind.
    otel = sorted(f for f in Settings.model_fields if "otel" in f or "telemetry" in f)
    assert otel == ["otel_export_endpoint", "otel_export_mode"]
    fields = " ".join(Settings.model_fields)
    for word in ("header", "trace", "metric", "exporter", "datadog", "grafana", "new_relic",
                 "sentry"):  # fmt: skip
        assert word not in fields, word
    assert Settings(_env_file=None).otel_export_mode == "disabled"
    example = (ROOT / ".env.example").read_text()
    assert not re.search(r"^(OTEL_|APP_DATADOG|APP_NEW_RELIC|APP_GRAFANA)", example,
                         re.MULTILINE)  # fmt: skip
    assert set(re.findall(r"^#?\s*(APP_OTEL_\w+)=", example, re.MULTILINE)) <= {
        "APP_OTEL_EXPORT_MODE", "APP_OTEL_EXPORT_ENDPOINT"}  # fmt: skip


def test_operator_factory_signature_is_unchanged_and_create_app_gains_one_option() -> None:
    # Task 034: the operator factory chooses the ONE Product observability of the
    # application (keyword-only, default: the Product's own) for create_app AND Workflows.
    assert list(inspect.signature(create_deployment_app).parameters) == [
        "settings", "runtime_settings", "model", "observability",
    ]  # fmt: skip
    parameters = inspect.signature(create_app).parameters
    # Task 031 appended ``integration_service`` after ``observability``; Task 032
    # appended ``agent_service``, Task 034 ``workflow_service`` and Task 035
    # ``knowledge_service``, Task 036 ``approval_service`` and Task 037
    # ``conversation_service``, and Task 039 ``system_probe``.
    assert list(parameters)[-8:] == ["observability", "integration_service", "agent_service",
                                     "workflow_service", "knowledge_service",
                                     "approval_service", "conversation_service",
                                     "system_probe"]  # fmt: skip
    assert parameters["observability"].default is None
    assert parameters["integration_service"].default is None
    assert parameters["agent_service"].default is None
    assert parameters["workflow_service"].default is None
    assert parameters["knowledge_service"].default is None
    assert parameters["approval_service"].default is None
    assert parameters["conversation_service"].default is None
    assert parameters["system_probe"].default is None


def test_product_http_surface_is_unchanged(settings, runtime_settings) -> None:
    app = create_app(settings, runtime_settings, observability=RecordingObservability())
    # Task 031's integration-management and Task 032's Agent-management routes are pinned
    # in tests/api; the observed Product surface below is unchanged (they are not observed
    # paths).
    product = sorted(
        (method, path) for method, path in effective_api_routes(app.routes)
        if (path.startswith("/api/")
            and not path.startswith(("/api/v1/integrations/", "/api/v1/agents",
                                     "/api/v1/skills/", "/api/v1/tasks/",
                                     "/api/v1/workflows/", "/api/v1/knowledge/",
                                     "/api/v1/approvals", "/api/v1/conversations",
                                     # Task 039: pinned in tests/api and tests/system.
                                     "/api/v1/system/")))
        or path == "/health"
    )  # fmt: skip
    assert product == [
        ("GET", "/api/v1/operations/reports/daily"),
        ("GET", "/api/v1/operations/tickets/commands"),
        ("GET", "/health"),
        ("POST", "/api/v1/operations/runs"),
        ("POST", "/api/v1/operations/tickets"),
    ]
    # No Product-owned observability route (AgentOS keeps its own, pre-existing, AgentOS-key
    # protected runtime routes; they are not Product routes).
    product_sources = [APP / "main.py", *modules(APP / "routes"), *modules(OBSERVABILITY)]
    for path in product_sources:
        source = path.read_text()
        for forbidden in ('"/metrics', '"/telemetry', '"/observability', '"/debug'):
            assert forbidden not in source, (path.name, forbidden)


def test_agno_telemetry_stays_disabled_with_product_observability(
    monkeypatch: pytest.MonkeyPatch, settings, runtime_settings
) -> None:
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)
    app = create_app(settings, runtime_settings, observability=RecordingObservability())
    assert app.state.agent_os.telemetry is False
    assert all(agent.telemetry is False for agent in app.state.agent_os.agents)
    monkeypatch.setenv("AGNO_TELEMETRY", "true")
    with pytest.raises(RuntimeConfigurationError):
        create_app(settings, runtime_settings, observability=RecordingObservability())


def test_operations_agent_has_no_observability_tool() -> None:
    from app.agents.operations import OPERATIONS_TOOL_CALL_LIMIT  # noqa: PLC0415
    from tests.agents.helpers import ops_stack  # noqa: PLC0415
    from tests.support.scripted_tool_model import Reply  # noqa: PLC0415

    agent = ops_stack([Reply("ok")]).agent
    assert [t.__name__ for t in agent.tools] == [  # type: ignore[union-attr]
        "get_order", "get_order_shipments", "get_daily_operations_report",
        "create_operational_ticket",
    ]  # fmt: skip
    assert agent.tool_call_limit == OPERATIONS_TOOL_CALL_LIMIT == 6
