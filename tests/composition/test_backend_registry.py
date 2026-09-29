"""Task 022: the Product-owned business backend allowlist and generic selection.

Settings validate the backend id's SYNTAX; the immutable registry decides whether it is
installed and where it may run; deployment selection is generic (no backend branch).
"""

import ast
import inspect
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import app
from app.bootstrap import create_deployment_app
from app.composition import (
    BusinessBackendRegistration,
    BusinessBackendRegistry,
    DeploymentComposition,
    DeploymentCompositionError,
    build_default_backend_registry,
    build_deployment_composition,
    local_mock,
)
from app.composition import deployment as deployment_module
from app.composition import registry as registry_module
from app.config import Settings
from tests.support.product_auth import deployment_settings
from tests.support.scripted_tool_model import ScriptedToolModel

APP_DIR = Path(app.__file__).parent
COMPOSITION_DIR = APP_DIR / "composition"
SELECTION_FILES = (COMPOSITION_DIR / "deployment.py", COMPOSITION_DIR / "registry.py",
                   COMPOSITION_DIR / "contracts.py", COMPOSITION_DIR / "__init__.py")  # fmt: skip
UNSUPPORTED = "unsupported business backend"
NOT_ALLOWED = "selected business backend is not allowed in this environment"


def make_settings(**values: Any) -> Settings:
    """Settings without any developer .env file (pydantic-settings' ``_env_file``)."""
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def env_settings(settings: Settings, environment: str, backend: str) -> Settings:
    if environment in ("staging", "production"):
        return deployment_settings(settings, environment, business_backend=backend)
    return make_settings(**(settings.model_dump() | {
        "environment": environment, "business_backend": backend}))  # fmt: skip


class RecordingBuilder:
    """A narrow TEST-ONLY backend builder returning a safe, empty composition."""

    def __init__(self) -> None:
        self.calls: list[tuple[Settings, Any]] = []
        self.result = DeploymentComposition()

    def __call__(self, settings: Settings, *, model: Any = None) -> DeploymentComposition:
        self.calls.append((settings, model))
        return self.result


def test_backend_registry(
    builder: RecordingBuilder, environments=frozenset({"local", "test"})
) -> BusinessBackendRegistry:
    return BusinessBackendRegistry(
        (
            BusinessBackendRegistration(
                backend_id="test-backend", allowed_environments=environments, builder=builder
            ),
        )
    )


test_backend_registry.__test__ = False  # type: ignore[attr-defined] - a helper, not a test


@pytest.fixture
def forbid_backend_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any backend builder, model build, engine or mock provider construction is recorded."""
    touched: list[str] = []

    def trap(name: str):
        def called(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"{name} must not run")

        return called

    monkeypatch.setattr(registry_module, "_build_mock_backend", trap("mock builder"))
    for name in ("build_default_model", "create_product_engine", "MockCommerceSystem",
                 "MockCommerceAdapter", "MockTicketDesk"):  # fmt: skip
        monkeypatch.setattr(local_mock, name, trap(name))
    return touched


# ----- Settings: syntax only ------------------------------------------------------------


@pytest.mark.parametrize("value", ["disabled", "mock", "test-backend", "not-registered",
                                   "commerce-a", "erp.v2", "backend_1", "a" * 64])  # fmt: skip
def test_syntactically_valid_ids_are_accepted_by_settings(value) -> None:
    assert make_settings(business_backend=value).business_backend == value


@pytest.mark.parametrize(
    "value",
    ["", " MOCK ", "Mock", "MOCK", "../mock", "app.module:Class", "mock/provider",
     "mock;something", "mock backend", "/tmp/plugin", " mock", "mock ", "mock\n", "-mock",  # noqa: S108
     ".mock", "_mock", "a" * 65, "mock\\x", "mock$", "https://x.invalid"],
)  # fmt: skip
def test_malformed_ids_are_rejected_and_never_normalized(value) -> None:
    with pytest.raises(ValidationError):
        make_settings(business_backend=value)


def test_dotted_ids_are_identifiers_never_module_paths(settings, forbid_backend_work) -> None:
    """The mandated pattern allows dots (e.g. ``erp.v2``), so ``app.module`` is a valid id
    SHAPE; it is still only a lookup key: unregistered -> unsupported, nothing imported."""
    for value in ("app.module", "app.composition.local_mock", "os.path"):
        s = env_settings(settings, "local", value)
        assert s.business_backend == value
        with pytest.raises(DeploymentCompositionError, match=UNSUPPORTED):
            build_deployment_composition(s)
    assert forbid_backend_work == []


def test_default_and_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().business_backend == "disabled"
    monkeypatch.setenv("APP_BUSINESS_BACKEND", "mock")
    assert make_settings().business_backend == "mock"
    monkeypatch.setenv("APP_BUSINESS_BACKEND", " Mock ")
    with pytest.raises(ValidationError):
        make_settings()


# ----- registrations and the registry ------------------------------------------------------


def test_valid_registration_and_resolution() -> None:
    builder = RecordingBuilder()
    registry = test_backend_registry(builder)
    registration = registry.resolve("test-backend")
    assert registration is not None and registration.builder is builder
    assert registration.allowed_environments == frozenset({"local", "test"})
    assert registry.resolve("not-registered") is None
    assert registry.resolve("disabled") is None
    assert registry.resolve(None) is None  # type: ignore[arg-type]
    assert registry.backend_ids == frozenset({"test-backend"})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"backend_id": "disabled"}, "reserved"),
        ({"allowed_environments": frozenset()}, "at least one environment"),
        ({"allowed_environments": frozenset({"moon"})}, "unknown environment"),
        ({"allowed_environments": {"local"}}, "at least one environment"),  # not frozen
        ({"backend_id": "Mock"}, "invalid"),
        ({"backend_id": "app.module:Class"}, "invalid"),
        ({"backend_id": "mock\n"}, "invalid"),
        ({"backend_id": ""}, "invalid"),
        ({"builder": "app.composition.local_mock:build_local_mock_composition"}, "callable"),
        ({"builder": None}, "callable"),
    ],
)
def test_invalid_registrations_are_rejected(kwargs, message) -> None:
    data: dict[str, Any] = {"backend_id": "test-backend",
                            "allowed_environments": frozenset({"local"}),
                            "builder": RecordingBuilder()} | kwargs  # fmt: skip
    with pytest.raises(ValueError, match=message):
        BusinessBackendRegistration(**data)


def test_duplicate_ids_fail_instead_of_last_one_wins() -> None:
    first, second = RecordingBuilder(), RecordingBuilder()
    entry = lambda b: BusinessBackendRegistration(  # noqa: E731
        backend_id="test-backend", allowed_environments=frozenset({"local"}), builder=b
    )
    with pytest.raises(ValueError, match="duplicate"):
        BusinessBackendRegistry((entry(first), entry(second)))
    with pytest.raises(TypeError):
        BusinessBackendRegistry(({"backend_id": "x"},))  # type: ignore[arg-type]


def test_registry_and_registrations_are_immutable() -> None:
    registry = build_default_backend_registry()
    for name in ("register", "unregister", "clear", "add", "update", "pop", "__setitem__"):
        assert not hasattr(registry, name), name
    with pytest.raises(AttributeError):
        registry._by_id = {}  # type: ignore[misc]
    with pytest.raises(AttributeError):
        registry.anything = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del registry._by_id
    with pytest.raises(TypeError):
        registry._by_id["x"] = None  # type: ignore[index]  # read-only mapping view
    assert isinstance(registry.backend_ids, frozenset)
    mock = registry.resolve("mock")
    assert mock is not None
    with pytest.raises(FrozenInstanceError):
        mock.allowed_environments = frozenset({"production"})  # type: ignore[misc]
    with pytest.raises(AttributeError):
        mock.allowed_environments.add("production")  # type: ignore[attr-defined]
    # A fresh default registry every time: nothing shared or mutable is handed out.
    assert build_default_backend_registry() is not registry


def test_default_registry_is_exactly_the_local_mock_plugin() -> None:
    registry = build_default_backend_registry()
    assert registry.backend_ids == frozenset({"mock"})
    mock = registry.resolve("mock")
    assert mock is not None
    assert mock.allowed_environments == frozenset({"local", "test"})
    assert registry.resolve("disabled") is None  # the sentinel is not a plugin


# ----- generic selection -------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["local", "test"])
def test_disabled_development_is_an_empty_composition(settings, forbid_backend_work,
                                                      environment) -> None:  # fmt: skip
    composition = build_deployment_composition(env_settings(settings, environment, "disabled"))
    assert composition == DeploymentComposition()
    assert forbid_backend_work == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_disabled_deployment_fails_closed(settings, forbid_backend_work, environment) -> None:
    with pytest.raises(DeploymentCompositionError, match="no business backend is available"):
        build_deployment_composition(env_settings(settings, environment, "disabled"))
    assert forbid_backend_work == []


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production"])
def test_unknown_backend_fails_safely_before_any_work(settings, forbid_backend_work,
                                                      environment) -> None:  # fmt: skip
    s = env_settings(settings, environment, "not-registered")
    with pytest.raises(DeploymentCompositionError) as info:
        build_deployment_composition(s, model=ScriptedToolModel())
    assert str(info.value) == UNSUPPORTED
    text = repr(info.value) + str(info.value.args)
    for leak in ("not-registered", str(s.database_url), "postgresql", "app.", "Settings"):
        assert leak not in text, leak
    assert info.value.__cause__ is None
    assert forbid_backend_work == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_mock_is_refused_in_deployments_before_the_builder(settings, forbid_backend_work,
                                                           environment) -> None:  # fmt: skip
    with pytest.raises(DeploymentCompositionError, match=NOT_ALLOWED):
        build_deployment_composition(env_settings(settings, environment, "mock"),
                                     model=ScriptedToolModel())  # fmt: skip
    assert forbid_backend_work == []  # no builder, model, engine or mock provider


@pytest.mark.parametrize("environment", ["local", "test"])
def test_mock_in_development_goes_through_the_registered_builder(settings, monkeypatch,
                                                                 environment) -> None:  # fmt: skip
    calls: list[str] = []
    original = registry_module._build_mock_backend

    def spy(settings_, *, model=None):
        calls.append(settings_.environment)
        return original(settings_, model=model)

    monkeypatch.setattr(registry_module, "_build_mock_backend", spy)
    composition = build_deployment_composition(env_settings(settings, environment, "mock"),
                                               model=ScriptedToolModel())  # fmt: skip
    assert calls == [environment]
    assert composition.daily_operations_service is not None
    assert composition.operations_service is not None
    composition.discard()


@pytest.mark.parametrize("environment", ["local", "test"])
def test_a_new_backend_needs_only_a_registration(settings, forbid_backend_work,
                                                 environment) -> None:  # fmt: skip
    builder = RecordingBuilder()
    model = ScriptedToolModel()
    s = env_settings(settings, environment, "test-backend")
    composition = build_deployment_composition(
        s, model=model, registry=test_backend_registry(builder)
    )
    assert composition is builder.result
    assert builder.calls == [(s, model)]  # exactly once, with the settings and model
    assert forbid_backend_work == []
    # The default allowlist does not know it.
    with pytest.raises(DeploymentCompositionError, match=UNSUPPORTED):
        build_deployment_composition(s, model=model)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_disallowed_custom_backend_builder_is_never_invoked(settings, environment) -> None:
    builder = RecordingBuilder()
    with pytest.raises(DeploymentCompositionError, match=NOT_ALLOWED):
        build_deployment_composition(env_settings(settings, environment, "test-backend"),
                                     registry=test_backend_registry(builder))  # fmt: skip
    assert builder.calls == []


def test_a_production_registration_is_honoured_generically(settings) -> None:
    """Environment policy is registration metadata, not a branch in the selector."""
    builder = RecordingBuilder()
    registry = test_backend_registry(builder, frozenset({"production"}))
    s = env_settings(settings, "production", "test-backend")
    assert build_deployment_composition(s, registry=registry) is builder.result
    with pytest.raises(DeploymentCompositionError, match=NOT_ALLOWED):
        build_deployment_composition(env_settings(settings, "local", "test-backend"),
                                     registry=registry)  # fmt: skip


def test_builder_results_and_injected_registries_are_checked(settings) -> None:
    def broken(settings: Settings, *, model: Any = None) -> Any:  # noqa: ARG001
        return {"operations_service": None}  # not a DeploymentComposition

    s = env_settings(settings, "local", "test-backend")
    registry = BusinessBackendRegistry(
        (
            BusinessBackendRegistration(
                backend_id="test-backend", allowed_environments=frozenset({"local"}), builder=broken
            ),
        )
    )
    with pytest.raises(DeploymentCompositionError, match="invalid composition"):
        build_deployment_composition(s, registry=registry)
    with pytest.raises(DeploymentCompositionError, match=UNSUPPORTED):
        build_deployment_composition(s, registry={"test-backend": RecordingBuilder()})  # type: ignore[arg-type]


# ----- operator factory and code shape ---------------------------------------------------------


def test_operator_factory_cannot_inject_a_registry_or_builder() -> None:
    params = inspect.signature(create_deployment_app).parameters
    assert list(params) == ["settings", "runtime_settings", "model"]
    assert "registry" in inspect.signature(build_deployment_composition).parameters
    bootstrap = (APP_DIR / "bootstrap.py").read_text()
    assert "registry" not in bootstrap.lower() and "builder" not in bootstrap.lower()


def test_selection_has_no_backend_specific_branch() -> None:
    source = (COMPOSITION_DIR / "deployment.py").read_text()
    tree = ast.parse(source)
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str)}  # fmt: skip
    assert "mock" not in strings and "test-backend" not in strings
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            assert "mock" not in ast.unparse(node), ast.unparse(node)
    assert "local_mock" not in source and "integrations" not in source


def test_no_configuration_driven_dynamic_import_exists() -> None:
    for path in SELECTION_FILES:
        tree = ast.parse(path.read_text())
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("importlib.import_module", "import_module", "__import__", "eval",
                          "exec", "compile", "getattr", "entry_points", "load_module",
                          "spec_from_file_location", "run_path"):  # fmt: skip
            assert forbidden not in calls, (path.name, forbidden)
        names = [m for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
                 for m in ([n.module or ""] if isinstance(n, ast.ImportFrom)
                           else [a.name for a in n.names])]  # fmt: skip
        assert not [m for m in names if m.startswith(("importlib", "pkgutil", "pkg_resources"))]
    # The only lazy import is the static, Product-chosen one inside the mock builder.
    tree = ast.parse((COMPOSITION_DIR / "registry.py").read_text())
    nested = [n for f in tree.body if isinstance(f, ast.FunctionDef)
              for n in ast.walk(f) if isinstance(n, ast.ImportFrom)]  # fmt: skip
    assert [(n.module, [a.name for a in n.names]) for n in nested] == [
        ("app.composition.local_mock", ["build_local_mock_composition"])
    ]


def test_new_runtime_files_name_no_external_company() -> None:
    allowed_concrete = {"mock"}
    registry = build_default_backend_registry()
    assert registry.backend_ids == allowed_concrete
    for path in SELECTION_FILES:
        text = path.read_text().lower()
        for word in ("shopify", "woocommerce", "magento", "salla", "zid", "amazon", "ebay",
                     "etsy", "bigcommerce", "odoo", "netsuite"):  # fmt: skip
            assert word not in text, (path.name, word)


def test_registry_concepts_stay_in_config_and_composition() -> None:
    for layer in ("routes", "services", "commerce", "governance", "execution", "commands",
                  "operations", "agents", "workflows", "persistence", "integrations",
                  "application", "context", "auth", "runtime"):  # fmt: skip
        for path in sorted((APP_DIR / layer).rglob("*.py")):
            text = path.read_text()
            for word in ("BusinessBackend", "backend_registry", "business_backend",
                         "app.composition"):  # fmt: skip
                assert word not in text, (path.relative_to(APP_DIR), word)
    main = (APP_DIR / "main.py").read_text()
    assert "registry" not in main.lower() and "business_backend" not in main


# ----- lazy imports (fresh interpreters) ---------------------------------------------------------


def run_isolated(body: str) -> list[str]:
    code = (
        "import sys\n"
        "from app.config import Settings, ProductApiKeyPrincipalConfig\n"
        "p = ProductApiKeyPrincipalConfig(key_id='k', key_sha256='0' * 64, actor_id='a')\n"
        "def settings(env, backend):\n"
        "    extra = {} if env in ('local', 'test') else dict(product_auth_mode='api_key',\n"
        "             company_id='c', product_api_keys=(p,))\n"
        "    return Settings(_env_file=None, environment=env, business_backend=backend,\n"
        "                    database_url='postgresql+psycopg://u@127.0.0.1:1/d', **extra)\n"
        f"{body}\n"
        "print(sorted(m for m in sys.modules if m.startswith(\n"
        "    ('app.composition.local_mock', 'app.integrations.commerce.mock'))))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    return result.stdout.strip().splitlines()


def test_importing_the_composition_loads_no_mock() -> None:
    body = "import app.composition, app.composition.deployment, app.composition.registry"
    assert run_isolated(body) == ["[]"]


@pytest.mark.parametrize(
    ("environment", "backend", "expected"),
    [
        ("local", "disabled", "ok"),
        ("test", "disabled", "ok"),
        ("local", "not-registered", UNSUPPORTED),
        ("production", "not-registered", UNSUPPORTED),
        ("staging", "mock", NOT_ALLOWED),
        ("production", "mock", NOT_ALLOWED),
    ],
)
def test_selection_that_does_not_build_the_mock_never_loads_it(environment, backend, expected):
    body = (
        "from app.composition import build_deployment_composition\n"
        "try:\n"
        f"    build_deployment_composition(settings({environment!r}, {backend!r}))\n"
        "    print('ok')\n"
        "except Exception as e:\n"
        "    print(e)\n"
    )
    assert run_isolated(body) == [expected, "[]"]


def test_allowed_mock_selection_loads_the_mock_lazily() -> None:
    body = (
        "from app.composition import build_deployment_composition\n"
        "assert 'app.composition.local_mock' not in sys.modules\n"
        "from app.runtime.non_executing_model import NonExecutingModel\n"
        "c = build_deployment_composition(settings('test', 'mock'), model=NonExecutingModel())\n"
        "c.discard()\n"
    )
    loaded = run_isolated(body)[-1]
    assert "'app.composition.local_mock'" in loaded
    assert "'app.integrations.commerce.mock'" in loaded


def test_deployment_module_selects_through_the_registry_only() -> None:
    assert deployment_module.build_default_backend_registry is build_default_backend_registry
