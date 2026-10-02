"""Architecture guards for Task 034 (the Product Workflow Platform)."""

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import app
import app.workflow_management

APP = Path(app.__file__).parent
ROOT = APP.parents[2]
PACKAGE = Path(app.workflow_management.__file__).parent
DAILY_PLATFORM = APP / "workflows" / "operations_daily_platform.py"
PLATFORM_FILES = sorted(PACKAGE.glob("*.py")) + [DAILY_PLATFORM]
PROVIDERS = ("shopify", "woocommerce", "whatsapp", "bosta", "shipblu", "meta ads",
             "google ads", "salla", "f" + "ulfly")  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_package_layout() -> None:
    assert {p.name for p in PACKAGE.glob("*.py")} == {
        "__init__.py", "definitions.py", "catalog.py", "state.py", "records.py", "handlers.py",
        "contracts.py", "engine.py", "service.py", "actions.py", "errors.py",
    }  # fmt: skip


def test_definitions_catalog_and_state_are_pure_product_metadata() -> None:
    for name in ("definitions.py", "catalog.py", "state.py"):
        for module in imports(PACKAGE / name):
            assert module.split(".")[0] in {"enum", "collections", "types", "typing",
                                            "pydantic"} or module.startswith(
                "app.workflow_management."), (name, module)  # fmt: skip


def test_the_platform_is_runtime_independent_and_never_calls_a_model() -> None:
    forbidden = ("agno", "openai", "anthropic", "app.agents", "app.runtime", "app.composition",
                 "app.persistence", "app.routes", "app.integrations", "app.integration_management",
                 "fastapi", "starlette", "sqlalchemy", "psycopg", "httpx", "requests", "socket",
                 "urllib", "aiohttp")  # fmt: skip
    for path in PLATFORM_FILES:
        for module in imports(path):
            assert not module.startswith(forbidden), (path.name, module)
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for word in ("Agent", "Model", "arun", "OperationsAgentRunner", "build_default_model",
                     "prompt", "completion"):  # fmt: skip
            assert word not in names | attrs, (path.name, word)


def test_no_dynamic_loading_providers_or_cloud_services() -> None:
    for path in PLATFORM_FILES:
        text = path.read_text()
        for call in ("importlib", "pkgutil", "runpy", "__import__(", "entry_points", "exec(",
                     "eval(", "import_module", "getattr(module", "globals()["):  # fmt: skip
            assert call not in text, (path.name, call)
        lower = text.lower()
        for provider in PROVIDERS:
            assert provider not in lower, (path.name, provider)
        for cloud in ("agno cloud", "control plane", "os.agno.com", "agno_api_key",
                      "api.agno.com"):  # fmt: skip
            assert cloud not in lower, (path.name, cloud)


def test_no_background_worker_scheduler_or_detached_task() -> None:
    workers = ("celery", "temporalio", "kafka", "aiokafka", "dramatiq", "prefect", "airflow",
               "apscheduler", "rq", "redis", "arq", "huey", "threading", "multiprocessing",
               "concurrent")  # fmt: skip
    for path in PLATFORM_FILES:
        text = path.read_text()
        for module in imports(path):
            assert module.split(".")[0] not in workers, (path.name, module)
        for spawn in ("create_task", "ensure_future", "call_later", "call_soon",
                      "run_in_executor", "TaskGroup", "gather("):  # fmt: skip
            assert spawn not in text, (path.name, spawn)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = " ".join(pyproject["project"]["dependencies"]).lower()
    for name in ("celery", "temporal", "kafka", "dramatiq", "prefect", "airflow", "apscheduler",
                 "tenacity", "backoff", "stamina"):  # fmt: skip
        assert name not in declared, name


def test_the_agno_dependency_is_the_unchanged_oss_pin() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    agno = [d for d in pyproject["project"]["dependencies"] if d.startswith("agno")]
    assert agno == ["agno[os,postgres,openai,anthropic]==3.0.11"]


def test_governance_execution_and_agents_never_depend_on_the_platform() -> None:
    for layer in ("governance", "execution", "agents", "commands", "context", "auth",
                  "integrations", "integration_management", "services", "operations",
                  "commerce", "runtime"):  # fmt: skip
        for path in (APP / layer).rglob("*.py"):
            for module in imports(path):
                assert not module.startswith("app.workflow_management"), path
    # Agent management only reads the static CATALOG (Task -> Workflow references).
    for path in (APP / "agent_management").rglob("*.py"):
        for module in imports(path):
            if module.startswith("app.workflow_management"):
                assert module == "app.workflow_management.catalog", (path.name, module)


def test_workflow_routes_are_get_only_and_there_is_no_generic_executor() -> None:
    source = (APP / "routes" / "workflows.py").read_text()
    assert re.findall(r"@router\.(\w+)\(", source) == ["get", "get", "get", "get"]
    for word in ("engine", "execute(", "resume(", "WorkflowEngine"):
        assert word not in source, word
    # Nothing outside the composition root and the daily adapter constructs or uses the
    # engine (no public run endpoint, no scheduler).
    users = {
        str(p.relative_to(APP)) for p in APP.rglob("*.py")
        if PACKAGE not in p.parents
        and any(m == "app.workflow_management.engine" for m in imports(p))
    }  # fmt: skip
    assert users == {"composition/local_mock.py", "workflows/operations_daily_platform.py"}


def test_compositions_validate_static_definitions_before_acquiring_resources() -> None:
    for name in ("local_mock.py", "workflows.py"):
        source = (APP / "composition" / name).read_text()
        assert source.index("build_default_workflow_catalog()") < source.index(
            "create_product_engine(str(settings.database_url))"), name  # fmt: skip


def test_migration_0005_persists_control_state_only() -> None:
    text = (ROOT / "apps/api/migrations/versions/0005_create_workflow_runtime.py").read_text()
    tables = re.findall(r"op\.create_table\(\s*(\w+)", text)
    assert tables == ["RUNS", "STEPS", "EVENTS"]
    assert re.findall(r'^(RUNS|STEPS|EVENTS) = "(\w+)"', text, re.MULTILINE) == [
        ("RUNS", "workflow_runs"),
        ("STEPS", "workflow_step_runs"),
        ("EVENTS", "workflow_events"),
    ]
    columns = set(re.findall(r'sa\.Column\(\s*"(\w+)"', text))
    assert columns == {
        "run_id", "workflow_id", "workflow_version", "request_id", "company_id", "actor_id",
        "actor_type", "channel", "store_id", "status", "current_step_id", "failure_code",
        "input_state", "input_fingerprint", "lease_owner", "lease_expires_at", "created_at",
        "updated_at", "completed_at", "step_id", "attempt", "handler_id", "verification_code",
        "checkpoint", "started_at", "sequence", "event_type", "occurred_at", "recorded_at",
    }  # fmt: skip
    for column in columns:
        for word in ("definition", "order", "shipment", "report", "prompt", "model", "output",
                     "secret", "credential", "payload", "message", "error"):  # fmt: skip
            assert word not in column, (column, word)
    assert "cascade" not in text.lower()
    migration = ROOT / "apps/api/migrations/versions/0005_create_workflow_runtime.py"
    assert not [m for m in imports(migration) if m.split(".")[0] == "app"]


def test_importing_the_workflow_routes_loads_no_persistence_or_engine() -> None:
    code = (
        "import sys, app.routes.workflows; "
        "print(sorted(m for m in sys.modules if m.startswith(('app.persistence', "
        "'app.workflow_management.engine', 'app.composition', 'alembic', 'sqlalchemy'))))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP.parent,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_test_only_workflows_live_only_in_tests() -> None:
    for path in APP.rglob("*.py"):
        assert "testing." not in path.read_text() or path.name == "__init__.py", path
