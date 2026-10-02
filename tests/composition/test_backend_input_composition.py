"""Task 025: deployment composition resolves exactly the declared backend inputs, in
order, fails closed with one fixed message, and keeps values out of everything else."""

import ast
import asyncio
import inspect
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

import app
from app.bootstrap import create_deployment_app
from app.composition import (
    BusinessBackendInputs,
    BusinessBackendInputSpec,
    BusinessBackendRegistration,
    BusinessBackendRegistry,
    DeploymentComposition,
    DeploymentCompositionError,
    FilesystemBusinessBackendInputSource,
    SecretValue,
    build_default_backend_registry,
    build_deployment_composition,
)
from app.composition import deployment as deployment_module
from app.composition import registry as registry_module
from app.composition.contracts import BACKEND_INPUTS_UNAVAILABLE
from app.config import Settings
from app.integrations.http import (
    HttpMethod,
    HttpxIntegrationTransport,
    IntegrationHttpRequest,
)
from tests.composition.test_backend_inputs import (
    CONFIG_MARKER,
    MARKERS,
    PATH_MARKER,
    SECRET_MARKER,
    SPEC,
)
from tests.integration_http.support import ChunkStream
from tests.support.product_auth import deployment_settings
from tests.support.scripted_tool_model import ScriptedToolModel

APP_DIR = Path(app.__file__).parent
ROOT = APP_DIR.parents[2]
COMPOSITION_DIR = APP_DIR / "composition"
BACKEND_INPUTS = COMPOSITION_DIR / "backend_inputs.py"
UNAVAILABLE = "business backend inputs are unavailable"
NOT_ALLOWED = "selected business backend is not allowed in this environment"
UNSUPPORTED = "unsupported business backend"


class RecordingBuilder:
    """A narrow TEST-ONLY builder that records what it receives."""

    def __init__(self) -> None:
        self.inputs: list[BusinessBackendInputs] = []
        self.result = DeploymentComposition()

    def __call__(self, settings: Settings, *, model: Any = None,
                 inputs: BusinessBackendInputs,
                 observability: Any = None) -> DeploymentComposition:  # fmt: skip
        self.inputs.append(inputs)
        return self.result


class RecordingSource:
    """A TEST-ONLY input source that records the specs it was asked for."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.specs: list[BusinessBackendInputSpec] = []
        self.result = result
        self.error = error

    def load(self, spec: BusinessBackendInputSpec) -> BusinessBackendInputs:
        self.specs.append(spec)
        if self.error is not None:
            raise self.error
        return self.result


def test_registry(
    builder, spec: BusinessBackendInputSpec = SPEC, environments=frozenset({"local", "test"})
) -> BusinessBackendRegistry:
    return BusinessBackendRegistry((BusinessBackendRegistration(
        backend_id="test-backend", allowed_environments=environments, builder=builder,
        input_spec=spec),))  # fmt: skip


test_registry.__test__ = False  # type: ignore[attr-defined] - a helper, not a test


def backend_settings(settings: Settings, environment: str = "test", backend: str = "test-backend",
                     **updates: Any) -> Settings:  # fmt: skip
    if environment in ("staging", "production"):
        return deployment_settings(settings, environment, business_backend=backend, **updates)
    data = settings.model_dump() | {"environment": environment, "business_backend": backend}
    return Settings(_env_file=None, **(data | updates))  # pyright: ignore[reportCallIssue]


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / f"config-{PATH_MARKER}"
    secrets = tmp_path / f"secrets-{PATH_MARKER}"
    config.mkdir()
    secrets.mkdir()
    (config / "BASE_URL").write_text("https://api.example.com")
    (config / "ACCOUNT_ID").write_text(CONFIG_MARKER)
    (secrets / "API_TOKEN").write_bytes(SECRET_MARKER)
    return config, secrets


@pytest.fixture
def forbid_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any filesystem source construction or file open is recorded and refused."""
    touched: list[str] = []

    def trap(name: str):
        def called(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"{name} must not run")

        return called

    monkeypatch.setattr(deployment_module, "FilesystemBusinessBackendInputSource",
                        trap("filesystem source"))  # fmt: skip
    monkeypatch.setattr(os, "open", trap("os.open"))
    return touched


def assert_safe(error: BaseException, *extra: str) -> None:
    assert str(error) == UNAVAILABLE
    text = f"{error!s} {error!r} {error.args!r}"
    for marker in (*MARKERS, "test-backend", "API_TOKEN", "BASE_URL", "ACCOUNT_ID", *extra):
        assert marker not in text, marker
    assert error.__cause__ is None and error.__suppress_context__ and error.__context__ is None


# ----- happy path -------------------------------------------------------------------------------


def test_declared_inputs_are_resolved_and_handed_to_the_builder(settings, roots) -> None:
    config, secrets = roots
    builder = RecordingBuilder()
    s = backend_settings(settings, backend_config_dir=config, backend_secrets_dir=secrets)
    composition = build_deployment_composition(s, registry=test_registry(builder))
    assert composition is builder.result
    [inputs] = builder.inputs
    assert type(inputs) is BusinessBackendInputs and inputs.matches(SPEC)
    assert inputs.config["ACCOUNT_ID"] == CONFIG_MARKER
    assert inputs.secrets["API_TOKEN"].reveal_bytes() == SECRET_MARKER


def test_a_production_backend_reads_inputs_generically(settings, roots) -> None:
    builder = RecordingBuilder()
    s = backend_settings(settings, "production", backend_config_dir=roots[0],
                         backend_secrets_dir=roots[1])  # fmt: skip
    registry = test_registry(builder, environments=frozenset({"production"}))
    assert build_deployment_composition(s, registry=registry) is builder.result
    assert builder.inputs[0].matches(SPEC)


def test_an_injected_source_is_asked_only_for_the_declared_spec(settings) -> None:
    builder = RecordingBuilder()
    result = BusinessBackendInputs(config={"BASE_URL": "a", "ACCOUNT_ID": "b"},
                                   secrets={"API_TOKEN": SecretValue(b"t")})  # fmt: skip
    source = RecordingSource(result)
    # No directories configured: an injected source does not need them.
    build_deployment_composition(backend_settings(settings), registry=test_registry(builder),
                                 input_source=source)  # fmt: skip
    assert source.specs == [SPEC]
    assert builder.inputs == [result]


# ----- order: nothing is read before the backend is resolved and allowed ------------------------


@pytest.mark.parametrize("environment", ["local", "test", "development-disabled"])
def test_disabled_reads_nothing(settings, forbid_reads, environment) -> None:
    env = "local" if environment == "development-disabled" else environment
    source = RecordingSource(error=AssertionError("must not load"))
    composition = build_deployment_composition(
        backend_settings(settings, env, "disabled"), input_source=source
    )
    assert composition.operations_service is None
    assert source.specs == [] and forbid_reads == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_disabled_in_a_deployment_reads_nothing(settings, forbid_reads, environment) -> None:
    source = RecordingSource(error=AssertionError("must not load"))
    with pytest.raises(DeploymentCompositionError):
        build_deployment_composition(backend_settings(settings, environment, "disabled"),
                                     input_source=source)  # fmt: skip
    assert source.specs == [] and forbid_reads == []


def test_unknown_backends_read_nothing(settings, forbid_reads) -> None:
    source = RecordingSource(error=AssertionError("must not load"))
    builder = RecordingBuilder()
    with pytest.raises(DeploymentCompositionError, match=UNSUPPORTED):
        build_deployment_composition(
            backend_settings(settings, backend="not-registered"),
            registry=test_registry(builder),
            input_source=source,
        )
    assert source.specs == [] and forbid_reads == [] and builder.inputs == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_disallowed_backends_read_nothing(settings, forbid_reads, roots, environment) -> None:
    source = RecordingSource(error=AssertionError("must not load"))
    builder = RecordingBuilder()
    s = backend_settings(settings, environment, backend_config_dir=roots[0],
                         backend_secrets_dir=roots[1])  # fmt: skip
    with pytest.raises(DeploymentCompositionError, match=NOT_ALLOWED):
        build_deployment_composition(s, registry=test_registry(builder), input_source=source)
    with pytest.raises(DeploymentCompositionError, match=NOT_ALLOWED):
        build_deployment_composition(s, registry=test_registry(builder))
    assert source.specs == [] and forbid_reads == [] and builder.inputs == []


def test_resolution_happens_after_the_environment_check_and_before_the_builder(settings) -> None:
    events: list[str] = []
    result = BusinessBackendInputs(config={"BASE_URL": "a", "ACCOUNT_ID": "b"},
                                   secrets={"API_TOKEN": SecretValue(b"t")})  # fmt: skip

    class Source:
        def load(self, spec):
            events.append("load")
            return result

    def builder(settings_, *, model=None, inputs, observability=None):
        events.append("builder")
        return DeploymentComposition()

    build_deployment_composition(backend_settings(settings), registry=test_registry(builder),
                                 input_source=Source())  # fmt: skip
    assert events == ["load", "builder"]


# ----- the mock backend ------------------------------------------------------------------------


def test_the_default_registry_is_still_exactly_the_mock_with_no_inputs() -> None:
    registry = build_default_backend_registry()
    assert registry.backend_ids == frozenset({"mock"})
    registration = registry.resolve("mock")
    assert registration is not None
    assert registration.input_spec == BusinessBackendInputSpec()
    assert registration.input_spec.is_empty
    assert registry.resolve("test-backend") is None


@pytest.mark.parametrize("environment", ["local", "test"])
def test_mock_reads_nothing_and_needs_no_directories(settings, forbid_reads, monkeypatch,
                                                      environment) -> None:  # fmt: skip
    received: list[BusinessBackendInputs] = []
    original = registry_module._build_mock_backend

    def spy(settings_, *, model=None, inputs, observability=None):
        received.append(inputs)
        return original(settings_, model=model, inputs=inputs, observability=observability)

    monkeypatch.setattr(registry_module, "_build_mock_backend", spy)
    source = RecordingSource(error=AssertionError("must not load"))
    s = backend_settings(settings, environment, "mock")
    assert s.backend_config_dir is None and s.backend_secrets_dir is None
    composition = build_deployment_composition(s, model=ScriptedToolModel(), input_source=source)
    try:
        assert composition.operations_service is not None
        empty = "BusinessBackendInputs(config_keys=0, secret_keys=0)"
        assert [repr(i) for i in received] == [empty]
        assert source.specs == [] and forbid_reads == []
    finally:
        composition.discard()


def test_the_mock_builder_refuses_non_empty_inputs(settings) -> None:
    unexpected = BusinessBackendInputs(config={"BASE_URL": "x"})
    bad_values: tuple[Any, ...] = (unexpected, None, {})
    for bad in bad_values:
        with pytest.raises(DeploymentCompositionError) as caught:
            registry_module._build_mock_backend(
                backend_settings(settings, backend="mock"), model=ScriptedToolModel(), inputs=bad
            )
        assert str(caught.value) == UNAVAILABLE


# ----- fail closed, with one fixed message -----------------------------------------------------


@pytest.mark.parametrize(
    ("config_dir", "secrets_dir"), [(None, "set"), ("set", None), (None, None)]
)
def test_missing_roots_fail_before_the_builder(settings, roots, config_dir, secrets_dir) -> None:
    builder = RecordingBuilder()
    s = backend_settings(settings, backend_config_dir=roots[0] if config_dir else None,
                         backend_secrets_dir=roots[1] if secrets_dir else None)  # fmt: skip
    with pytest.raises(DeploymentCompositionError) as caught:
        build_deployment_composition(s, registry=test_registry(builder))
    assert_safe(caught.value)
    assert builder.inputs == []


def test_a_root_is_needed_only_for_the_declared_kind(settings, roots) -> None:
    builder = RecordingBuilder()
    secrets_only = BusinessBackendInputSpec(secret_keys=frozenset({"API_TOKEN"}))
    s = backend_settings(settings, backend_secrets_dir=roots[1])
    build_deployment_composition(s, registry=test_registry(builder, secrets_only))
    config_only = BusinessBackendInputSpec(config_keys=frozenset({"BASE_URL"}))
    s = backend_settings(settings, backend_config_dir=roots[0])
    build_deployment_composition(s, registry=test_registry(builder, config_only))
    assert [set(i.secrets) | set(i.config) for i in builder.inputs] == [{"API_TOKEN"}, {"BASE_URL"}]


@pytest.mark.parametrize("remove", ["BASE_URL", "ACCOUNT_ID", "API_TOKEN"])
def test_a_missing_input_file_fails_safely(settings, roots, remove) -> None:
    config, secrets = roots
    ((secrets if remove == "API_TOKEN" else config) / remove).unlink()
    builder = RecordingBuilder()
    s = backend_settings(settings, backend_config_dir=config, backend_secrets_dir=secrets)
    with pytest.raises(DeploymentCompositionError) as caught:
        build_deployment_composition(s, registry=test_registry(builder))
    assert_safe(caught.value, str(config), str(secrets))
    assert builder.inputs == []


def test_a_symlinked_secret_fails_safely(settings, roots, tmp_path) -> None:
    config, secrets = roots
    (secrets / "API_TOKEN").unlink()
    (tmp_path / "elsewhere").write_bytes(SECRET_MARKER)
    (secrets / "API_TOKEN").symlink_to(tmp_path / "elsewhere")
    s = backend_settings(settings, backend_config_dir=config, backend_secrets_dir=secrets)
    with pytest.raises(DeploymentCompositionError) as caught:
        build_deployment_composition(s, registry=test_registry(RecordingBuilder()))
    assert_safe(caught.value)


@pytest.mark.parametrize(
    "error",
    [RuntimeError(f"{PATH_MARKER} {SECRET_MARKER!r}"), OSError(2, "No such file", PATH_MARKER),
     ValueError(CONFIG_MARKER), KeyError("API_TOKEN")],
)  # fmt: skip
def test_any_source_failure_becomes_one_fixed_error(settings, error) -> None:
    builder = RecordingBuilder()
    with pytest.raises(DeploymentCompositionError) as caught:
        build_deployment_composition(backend_settings(settings), registry=test_registry(builder),
                                     input_source=RecordingSource(error=error))  # fmt: skip
    assert_safe(caught.value)
    assert builder.inputs == []


def full_config() -> dict[str, str]:
    return {"BASE_URL": "a", "ACCOUNT_ID": "b"}


def token() -> dict[str, SecretValue]:
    return {"API_TOKEN": SecretValue(SECRET_MARKER)}


class LyingInputs(BusinessBackendInputs):
    """A subclass is not the Product's inputs type."""


@pytest.mark.parametrize(
    "result",
    [
        None, {}, {"config": full_config(), "secrets": token()}, "inputs",
        BusinessBackendInputs(config=full_config()),  # missing secret
        BusinessBackendInputs(secrets=token()),  # missing config
        BusinessBackendInputs(config=full_config() | {"EXTRA": "x"}, secrets=token()),
        BusinessBackendInputs(config=full_config(), secrets=token() | {"EXTRA": SecretValue(b"")}),
        BusinessBackendInputs(config={"BASE_URL": "a", "API_TOKEN": "t"},
                              secrets={"ACCOUNT_ID": SecretValue(b"b")}),
        BusinessBackendInputs(),
        LyingInputs(config=full_config(), secrets=token()),
    ],
)  # fmt: skip
def test_a_malformed_source_result_never_reaches_the_builder(settings, result) -> None:
    builder = RecordingBuilder()
    with pytest.raises(DeploymentCompositionError) as caught:
        build_deployment_composition(backend_settings(settings), registry=test_registry(builder),
                                     input_source=RecordingSource(result))  # fmt: skip
    assert_safe(caught.value)
    assert builder.inputs == []


def test_failures_do_not_log_values(settings, roots, caplog) -> None:
    caplog.set_level("DEBUG")
    config, secrets = roots
    (config / "BASE_URL").unlink()
    s = backend_settings(settings, backend_config_dir=config, backend_secrets_dir=secrets)
    with pytest.raises(DeploymentCompositionError):
        build_deployment_composition(s, registry=test_registry(RecordingBuilder()))
    for marker in MARKERS:
        assert marker not in caplog.text


# ----- operator factory -------------------------------------------------------------------------


def test_operator_factory_signature_is_unchanged() -> None:
    # Task 034: ``observability`` (keyword-only) chooses the application's ONE Product
    # observability; it is never a registry, builder, input source or credential.
    assert list(inspect.signature(create_deployment_app).parameters) == [
        "settings", "runtime_settings", "model", "observability",
    ]  # fmt: skip
    parameters = inspect.signature(build_deployment_composition).parameters
    assert parameters["input_source"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["input_source"].default is None


def test_settings_dumps_contain_paths_only(settings, roots) -> None:
    s = backend_settings(settings, backend_config_dir=roots[0], backend_secrets_dir=roots[1])
    dumped = s.model_dump_json() + repr(s)
    assert "BACKEND-SECRET-MARKER-" not in dumped and "BACKEND-CONFIG-MARKER-" not in dumped


# ----- test-only demo: a builder authenticates the Task 024 transport ---------------------------


def test_a_builder_reveals_the_secret_only_into_the_transport_header(settings, roots) -> None:
    """TEST-ONLY: how a future backend builder uses its inputs with the Product HTTP
    transport. Offline: an in-process MockTransport, never the network."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=ChunkStream([b'{"ok": true}']))

    built: list[HttpxIntegrationTransport] = []

    def builder(settings_, *, model=None, inputs: BusinessBackendInputs, observability=None):
        token = inputs.secrets["API_TOKEN"].reveal_bytes().decode("ascii")
        built.append(HttpxIntegrationTransport(
            inputs.config["BASE_URL"], default_headers={"Authorization": f"Bearer {token}"},
            transport=httpx.MockTransport(handler)))  # fmt: skip
        return DeploymentComposition()

    config, secrets = roots
    s = backend_settings(settings, backend_config_dir=config, backend_secrets_dir=secrets)
    build_deployment_composition(s, registry=test_registry(builder))
    [transport] = built

    async def call():
        try:
            return await transport.request(IntegrationHttpRequest(HttpMethod.GET, "/v1/orders"))
        finally:
            await transport.close()

    response = asyncio.run(call())
    assert response.status_code == 200
    [request] = seen
    assert request.headers["authorization"] == "Bearer " + SECRET_MARKER.decode()
    assert str(request.url) == "https://api.example.com/v1/orders"
    assert "BACKEND-SECRET-MARKER-" not in repr(transport)


# ----- architecture guards -----------------------------------------------------------------------


def calls_and_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def imported_modules(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_the_loader_never_reads_the_environment_scans_or_logs() -> None:
    names = calls_and_attributes(BACKEND_INPUTS)
    for forbidden in (
        "environ",
        "getenv",
        "putenv",
        "iterdir",
        "glob",
        "rglob",
        "walk",
        "scandir",
        "listdir",
        "fwalk",
        "chmod",
        "chown",
        "mkdir",
        "makedirs",
        "write",
        "write_text",
        "write_bytes",
        "unlink",
        "remove",
        "rename",
        "touch",
        "print",
        "getLogger",
        "logger",
        "log",
        "load_dotenv",
        "dotenv_values",
        "expanduser",
        "expandvars",
        "realpath",
        "resolve",
    ):
        assert forbidden not in names, forbidden
    modules = imported_modules(BACKEND_INPUTS)
    for forbidden in ("logging", "glob", "fnmatch", "dotenv", "shutil", "tempfile",
                      "subprocess", "httpx", "json", "pickle", "structlog", "opentelemetry",
                      "app.observability", "agno"):  # fmt: skip
        hits = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
        assert hits == [], forbidden


def test_composition_never_reads_the_environment_for_backend_credentials() -> None:
    for path in (COMPOSITION_DIR / "deployment.py", COMPOSITION_DIR / "registry.py",
                 COMPOSITION_DIR / "contracts.py", BACKEND_INPUTS):  # fmt: skip
        names = calls_and_attributes(path)
        assert "environ" not in names and "getenv" not in names, path.name


def test_business_layers_never_see_secret_values_or_backend_inputs() -> None:
    for layer in ("domain", "commerce", "agents", "workflows", "routes", "services",
                  "governance", "execution", "operations", "application", "commands",
                  "persistence", "context", "auth", "observability", "runtime",
                  "integrations"):  # fmt: skip
        directory = APP_DIR / layer
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            text = path.read_text()
            for name in ("SecretValue", "BusinessBackendInputs", "backend_inputs",
                         "BusinessBackendInputSource", "backend_secrets_dir",
                         "backend_config_dir"):  # fmt: skip
                assert name not in text, (str(path.relative_to(APP_DIR)), name)


def test_no_raw_backend_secret_settings_or_env_entries() -> None:
    example = (ROOT / ".env.example").read_text()
    assert re.search(r"^APP_BACKEND_CONFIG_DIR=$", example, re.MULTILINE)
    assert re.search(r"^APP_BACKEND_SECRETS_DIR=$", example, re.MULTILINE)
    sources = "\n".join(p.read_text() for p in sorted(APP_DIR.rglob("*.py")))
    for forbidden in ("APP_BACKEND_API_TOKEN", "APP_BACKEND_PASSWORD",
                      "APP_INTEGRATION_SECRETS_JSON", "backend_api_token", "backend_password",
                      "integration_secrets_json"):  # fmt: skip
        assert forbidden not in example and forbidden not in sources, forbidden
    for line in example.splitlines():
        if line.startswith("APP_BACKEND_"):
            assert line in ("APP_BACKEND_CONFIG_DIR=", "APP_BACKEND_SECRETS_DIR="), line


def test_no_secret_files_or_deployment_layouts_in_the_repository() -> None:
    listing = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["git", "-C", str(ROOT), "ls-files"],  # noqa: S607 - git from PATH, as CI does
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    tracked = listing.stdout.splitlines()
    assert tracked, "git ls-files returned nothing"
    for path in tracked:
        name = path.rsplit("/", 1)[-1]
        assert name not in ("API_TOKEN", "BASE_URL", "ACCOUNT_ID", "PASSWORD", "SECRET"), path
        # Only the layout README, the generic template (Task 026) and the local demo
        # override (Task 030, no values: its credentials are generated, git-ignored runtime
        # state); no per-deployment directories, backend input files or secrets.
        assert not path.startswith("deployments/") or path in (
            "deployments/README.md", "deployments/template/README.md",
            "deployments/template/compose.yaml", "deployments/template/.env.example",
            "deployments/demo/README.md", "deployments/demo/compose.override.yaml",
        ), path  # fmt: skip


def test_markers_are_not_committed_outside_tests() -> None:
    for path in [*sorted(APP_DIR.rglob("*.py")), ROOT / "README.md", ROOT / "apps" / "api" /
                 "README.md", ROOT / ".env.example"]:  # fmt: skip
        text = path.read_text()
        for marker in MARKERS:
            assert marker not in text, (path.name, marker)


def test_no_zeroization_claim_and_required_documentation() -> None:
    docs = (ROOT / "README.md").read_text() + (ROOT / "apps" / "api" / "README.md").read_text()
    source = BACKEND_INPUTS.read_text()
    for text in (docs, source):
        lowered = text.lower()
        assert "guarantees zeroization" not in lowered and "securely wiped" not in lowered
        assert "zeroization" in lowered  # stated as NOT guaranteed
    for phrase in ("APP_BACKEND_CONFIG_DIR", "APP_BACKEND_SECRETS_DIR", "no directory",
                   "rotation or hot reload", "vault", "cloud secret", "HSM", "prompts",
                   "OPENAI_API_KEY", "APP_DATABASE_URL", "APP_PRODUCT_API_KEYS",
                   "HttpxIntegrationTransport", "names"):  # fmt: skip
        assert phrase in docs, phrase


def test_the_filesystem_source_is_the_only_default() -> None:
    source = (COMPOSITION_DIR / "deployment.py").read_text()
    assert source.count("FilesystemBusinessBackendInputSource(") == 1
    assert FilesystemBusinessBackendInputSource.__module__ == "app.composition.backend_inputs"
    assert BACKEND_INPUTS_UNAVAILABLE == UNAVAILABLE
