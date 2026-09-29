"""Composition boundaries: who may compose, where mocks live, no startup migrations."""

import ast
import subprocess
import sys
from pathlib import Path

import app

APP_DIR = Path(app.__file__).parent
ROOT = APP_DIR.parents[2]
COMPOSITION_DIR = APP_DIR / "composition"
BOOTSTRAP = APP_DIR / "bootstrap.py"
MOCK_PACKAGE = "app.integrations.commerce.mock"


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def module_level_imports(path: Path) -> list[str]:
    found = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def app_files() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def test_only_bootstrap_depends_on_the_composition_root() -> None:
    importers = {
        str(p.relative_to(APP_DIR))
        for p in app_files()
        if COMPOSITION_DIR not in p.parents
        and any(m.startswith(("app.composition", "app.bootstrap")) for m in imports(p))
    }
    assert importers == {"bootstrap.py"}


def test_low_level_factory_composes_nothing() -> None:
    banned = ("app.composition", "app.bootstrap", "app.persistence", "app.commands",
              "app.execution", "app.governance", "app.operations", "app.agents",
              "app.integrations", "app.application", "sqlalchemy")  # fmt: skip
    bad = [m for m in imports(APP_DIR / "main.py") if m.startswith(banned)]
    assert bad == []
    source = (APP_DIR / "main.py").read_text()
    for name in ("PostgresWriteCommandStore", "PostgresAuditSink", "GovernanceGate",
                 "ExecutionCoordinator", "WriteCommandCoordinator", "create_product_engine",
                 "Mock", "business_backend", "build_operations_agent"):  # fmt: skip
        assert name not in source, name


def test_mock_integrations_are_imported_only_by_the_local_mock_composition() -> None:
    importers = {
        str(p.relative_to(APP_DIR))
        for p in app_files()
        if "mock" not in p.relative_to(APP_DIR).parts[:-1]
        and any(m.startswith(MOCK_PACKAGE) for m in imports(p))
    }
    assert importers == {"composition/local_mock.py"}
    # The generic selector never imports it at module level (only after the policy check).
    deployment = COMPOSITION_DIR / "deployment.py"
    assert not [m for m in module_level_imports(deployment) if m.startswith(MOCK_PACKAGE)]
    assert "app.composition.local_mock" not in module_level_imports(deployment)


def test_production_mock_composition_never_loads_the_mock_package() -> None:
    code = (
        "import sys\n"
        "from agno.os.settings import AgnoAPISettings\n"
        "from app.bootstrap import create_deployment_app\n"
        "from app.config import Settings, ProductApiKeyPrincipalConfig\n"
        "assert not [m for m in sys.modules if m.startswith('app.integrations.commerce.mock')]\n"
        "p = ProductApiKeyPrincipalConfig(key_id='k', key_sha256='0' * 64, actor_id='a')\n"
        "s = Settings(_env_file=None, environment='production', product_auth_mode='api_key',\n"
        "             company_id='c', product_api_keys=(p,), business_backend='mock',\n"
        "             database_url='postgresql+psycopg://u@127.0.0.1:1/d')\n"
        "try:\n"
        "    create_deployment_app(s, AgnoAPISettings(os_security_key='x' * 40))\n"
        "except Exception as e:\n"
        "    print(type(e).__name__, str(e))\n"
        "print(sorted(m for m in sys.modules if m.startswith(\n"
        "    ('app.integrations.commerce.mock', 'app.composition.local_mock',\n"
        "     'app.persistence', 'app.agents.operations'))))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    lines = result.stdout.strip().splitlines()
    assert lines[0] == (
        "DeploymentCompositionError selected business backend is not allowed in this environment"
    )
    assert lines[1] == "[]"


def test_business_layers_never_import_the_composition_root() -> None:
    for layer in ("routes", "services", "commerce", "execution", "governance", "persistence",
                  "commands", "operations", "agents", "integrations", "application",
                  "runtime", "auth", "context"):  # fmt: skip
        for path in sorted((APP_DIR / layer).rglob("*.py")):
            bad = [m for m in imports(path) if m.startswith(("app.composition", "app.bootstrap"))]
            assert bad == [], f"{path.relative_to(APP_DIR)}: {bad}"


def test_startup_never_migrates_or_creates_tables() -> None:
    for path in [BOOTSTRAP, *sorted(COMPOSITION_DIR.rglob("*.py"))]:
        tree = ast.parse(path.read_text())
        assert not [m for m in imports(path) if m.startswith("alembic")], path.name
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("create_all", "upgrade", "command.upgrade", "run_migrations"):
            assert not [c for c in calls if c.endswith(forbidden)], (path.name, forbidden)


def test_no_module_global_engine_or_session() -> None:
    for path in [BOOTSTRAP, *sorted(COMPOSITION_DIR.rglob("*.py"))]:
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                value = ast.unparse(node.value)
                for forbidden in ("create_product_engine", "create_async_engine",
                                  "create_session_factory", "sessionmaker"):  # fmt: skip
                    assert forbidden not in value, (path.name, value)
        assert "lru_cache" not in path.read_text() and "global " not in path.read_text()


def test_no_real_provider_backend_was_invented() -> None:
    # Task 022: the allowlist holds exactly the mock plugin; no real backend exists yet.
    from app.composition import build_default_backend_registry

    assert build_default_backend_registry().backend_ids == frozenset({"mock"})
    assert sorted(p.name for p in (APP_DIR / "integrations" / "commerce").iterdir()
                  if p.is_dir() and p.name != "__pycache__") == ["mock"]  # fmt: skip


def test_no_new_product_migration() -> None:
    versions = ROOT / "apps" / "api" / "migrations" / "versions"
    assert sorted(p.name for p in versions.glob("*.py")) == [
        "0001_create_write_commands.py",
        "0002_create_audit_events.py",
    ]


def test_documented_operator_command_uses_the_deployment_factory() -> None:
    command = "app.bootstrap:create_deployment_app --factory"
    for doc in (ROOT / "README.md", ROOT / "apps" / "api" / "README.md"):
        assert command in doc.read_text(), doc.name
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert command in workflow
    env = (ROOT / ".env.example").read_text()
    assert "APP_BUSINESS_BACKEND=disabled" in env
