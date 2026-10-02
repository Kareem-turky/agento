"""Architecture guards for Task 031 (provider-agnostic integration management)."""

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import app
import app.integration_management
from app.integration_management import IntegrationConnectionDriver, IntegrationSecretStore

APP_DIR = Path(app.__file__).parent
ROOT = APP_DIR.parents[2]
PACKAGE = Path(app.integration_management.__file__).parent
DOMAIN = ("definitions.py", "connections.py", "drivers.py", "catalog.py", "secrets.py")
STDLIB = {"__future__", "collections", "dataclasses", "datetime", "enum", "types", "typing",
          "uuid", "urllib"}  # fmt: skip


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
        "__init__.py", *DOMAIN, "filesystem_secrets.py", "actions.py", "handlers.py",
        "service.py",
    }  # fmt: skip


def test_domain_depends_only_on_the_standard_library_and_pydantic() -> None:
    for name in DOMAIN:
        for module in imports(PACKAGE / name):
            root = module.split(".")[0]
            assert root in STDLIB | {"pydantic"} or module.startswith(
                "app.integration_management."
            ), (name, module)


def test_application_layer_uses_only_governance_execution_and_context() -> None:
    allowed_app = ("app.integration_management", "app.governance", "app.execution",
                   "app.context")  # fmt: skip
    for name in ("actions.py", "handlers.py", "service.py", "filesystem_secrets.py"):
        for module in imports(PACKAGE / name):
            if module.startswith("app."):
                assert module.startswith(allowed_app), (name, module)
            else:
                assert module.split(".")[0] in STDLIB | {"pydantic", "asyncio", "errno",
                                                         "json", "os", "secrets", "stat",
                                                         "pathlib"}, (name, module)  # fmt: skip


def test_no_dynamic_loading_network_clients_frameworks_or_providers() -> None:
    forbidden_modules = ("importlib", "pkgutil", "runpy", "httpx", "requests", "urllib.request",
                         "socket", "http.client", "aiohttp", "fastapi", "sqlalchemy", "agno",
                         "openai", "anthropic")  # fmt: skip
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        for module in imports(path):
            assert not module.startswith(forbidden_modules), (path.name, module)
        for call in ("__import__(", "entry_points", "exec(", "eval(", "import_module"):
            assert call not in text, (path.name, call)
        for provider in ("shopify", "woocommerce", "f" + "ulfly", "whatsapp", "bosta", "shipblu",
                         "meta ads", "google ads"):  # fmt: skip
            assert provider not in text.lower(), (path.name, provider)


def test_the_driver_contract_is_connection_management_only() -> None:
    members = {name for name, _ in inspect.getmembers(IntegrationConnectionDriver)
               if not name.startswith("_")}  # fmt: skip
    assert members == {"integration_id", "validate_config", "test_connection", "aclose"}
    secret_members = {name for name, _ in inspect.getmembers(IntegrationSecretStore)
                      if not name.startswith("_")}  # fmt: skip
    assert secret_members == {"replace", "field_names", "read", "delete"}


def test_secret_values_never_leave_through_the_service_or_routes() -> None:
    from app.integration_management.service import IntegrationManagementService

    for name, member in inspect.getmembers(IntegrationManagementService, inspect.isfunction):
        if name.startswith("_"):
            continue
        annotation = str(inspect.signature(member).return_annotation)
        assert "Secret" not in annotation, name
    routes = (APP_DIR / "routes" / "integrations.py").read_text()
    assert "get_secret_value" not in routes and ".read(" not in routes


def test_business_layers_and_agents_never_use_integration_management() -> None:
    for layer in ("agents", "workflows", "integrations", "commerce", "operations", "services"):
        for path in (APP_DIR / layer).rglob("*.py"):
            for module in imports(path):
                assert not module.startswith("app.integration_management"), (layer, path.name)


def test_the_default_business_backend_registry_is_unchanged() -> None:
    from app.composition import build_default_backend_registry

    assert build_default_backend_registry().backend_ids == frozenset({"mock"})


def test_the_superseded_native_commerce_store_is_absent() -> None:
    """Task 031 (replacement) must not carry the rejected Product-owned commerce mirror."""
    forbidden = ("PostgresCommerceStore", "NativeCommerceAdapter", "CommerceStoreReader",
                 "native-commerce", "commerce_orders", "commerce_inventory_levels",
                 "commerce_companies")  # fmt: skip
    roots = [APP_DIR, ROOT / "apps" / "api" / "migrations", ROOT / "tests"]
    me = Path(__file__).resolve()
    for root in roots:
        for path in root.rglob("*.py"):
            if path.resolve() == me or path.name == "test_architecture.py":
                continue  # guard files name the forbidden identifiers on purpose
            text = path.read_text()
            for name in forbidden:
                assert name not in text, (str(path.relative_to(ROOT)), name)
    assert not (APP_DIR / "integrations" / "commerce" / "native").exists()
    assert not (APP_DIR / "commerce" / "store.py").exists()


def test_importing_the_routes_loads_no_persistence_or_secret_store() -> None:
    code = (
        "import sys, app.routes.integrations, app.main; "
        "print(sorted(m for m in sys.modules if m.startswith(('app.persistence', "
        "'app.integration_management.filesystem_secrets', 'app.composition', 'alembic'))))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"
