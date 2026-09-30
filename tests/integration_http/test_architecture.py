"""Boundaries of the integration HTTP transport: infrastructure for future adapters only."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

import app
from app.composition import build_default_backend_registry

APP = Path(app.__file__).parent
HTTP = APP / "integrations" / "http"
DOCSTRINGS = ('"""', "'''")


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


ALLOWED_ROOTS = {"asyncio", "collections", "dataclasses", "enum", "http", "math", "re",
                 "typing", "urllib", "zlib", "httpx"}  # fmt: skip


# Only the defensive dependency-log suppression module may touch logging (to filter
# httpx/httpcore records); it needs contextvars/contextlib for its per-task flag.
SUPPRESSION_MODULE = "dependency_logging.py"
SUPPRESSION_ONLY_ROOTS = {"logging", "contextlib", "contextvars"}


def test_transport_imports_only_stdlib_httpx_and_itself() -> None:
    for path in modules(HTTP):
        allowed = ALLOWED_ROOTS | (SUPPRESSION_ONLY_ROOTS if path.name == SUPPRESSION_MODULE
                                   else set())  # fmt: skip
        for name in imported(path):
            if name.startswith("app."):
                assert name.startswith("app.integrations.http"), (path.name, name)
            else:
                assert name.split(".")[0] in allowed, (path.name, name)


@pytest.mark.parametrize(
    "forbidden",
    ["fastapi", "starlette", "agno", "app.agents", "app.workflows", "app.governance",
     "app.execution", "app.commands", "app.persistence", "redis", "app.observability",
     "app.composition", "app.integrations.commerce", "opentelemetry",
     "app.routes", "app.services", "app.context", "app.runtime", "sqlalchemy"],
)  # fmt: skip
def test_transport_depends_on_no_product_layer_or_framework(forbidden: str) -> None:
    for path in modules(HTTP):
        for name in imported(path):
            assert not (name == forbidden or name.startswith(forbidden + ".")), (path.name, name)


def test_only_the_transport_package_itself_uses_it_for_now() -> None:
    """Agents, workflows, routes, services and every other layer stay unaware of it
    until a reviewed provider adapter exists; models never receive a transport."""
    offenders = [
        str(path.relative_to(APP)) for path in modules(APP)
        if HTTP not in path.parents
        and any(name.startswith("app.integrations.http") for name in imported(path))
    ]  # fmt: skip
    assert offenders == []


def test_only_the_transport_package_imports_httpx_in_the_product() -> None:
    offenders = [
        str(path.relative_to(APP)) for path in modules(APP)
        if HTTP not in path.parents and any(n.split(".")[0] == "httpx" for n in imported(path))
    ]  # fmt: skip
    assert offenders == []


def test_importing_the_product_integrations_does_not_load_the_transport() -> None:
    code = (
        "import sys; import app.integrations, app.integrations.commerce, "
        "app.integrations.commerce.mock, app.composition, app.workflows, app.agents; "
        "print(sorted(m for m in sys.modules if m.startswith('app.integrations.http')))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP.parent,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_no_provider_names_settings_or_backend_registration() -> None:
    """Identifiers and plain strings only (docstrings may explain what is ignored)."""
    words = {"shopify", "woocommerce", "salla", "zid", "mock", "backend", "registry",
             "getenv", "environ", "settings"}  # fmt: skip
    for path in modules(HTTP):
        if path.name != SUPPRESSION_MODULE:
            words_here = words | {"logger", "logging"}
        else:
            words_here = words
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME and token.string != "dependency_logging":
                parts = set(token.string.lower().split("_")) | {token.string.lower()}
                assert not parts & words_here, (path.name, token.string)
            if token.type == tokenize.STRING and not token.string.startswith(DOCSTRINGS):
                assert "APP_" not in token.string, (path.name, token.string)
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})


def test_no_new_settings_for_http_or_proxies() -> None:
    from app.config import Settings  # noqa: PLC0415

    for field in Settings.model_fields:
        parts = set(field.lower().split("_"))
        assert not parts & {"proxy", "http", "https", "insecure", "verify", "tls", "outbound"}, (
            field
        )


def test_operations_agent_gets_no_http_tool() -> None:
    from app.agents.operations import OPERATIONS_TOOL_CALL_LIMIT  # noqa: PLC0415
    from tests.agents.helpers import ops_stack  # noqa: PLC0415
    from tests.support.scripted_tool_model import Reply  # noqa: PLC0415

    agent = ops_stack([Reply("ok")]).agent
    assert [t.__name__ for t in agent.tools] == [  # type: ignore[union-attr]
        "get_order", "get_order_shipments", "get_daily_operations_report",
        "create_operational_ticket",
    ]  # fmt: skip
    assert agent.tool_call_limit == OPERATIONS_TOOL_CALL_LIMIT == 6


def test_httpx_is_an_explicit_runtime_pin_not_a_dev_one() -> None:
    import tomllib  # noqa: PLC0415

    pyproject = tomllib.loads((APP.parents[2] / "pyproject.toml").read_text())
    assert "httpx==0.28.1" in pyproject["project"]["dependencies"]
    assert not any(d.startswith("httpx") for d in pyproject["dependency-groups"]["dev"])


# ----- dependency-log suppression: the only logging use, and it emits nothing --------------

EMITTING = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log",
            "fatal"}  # fmt: skip
MUTATING = {"setLevel", "disable", "basicConfig", "dictConfig", "fileConfig", "addHandler",
            "removeHandler", "removeFilter", "setLoggerClass", "setLogRecordFactory",
            "captureWarnings"}  # fmt: skip


def test_logging_is_used_only_to_attach_the_suppression_filter() -> None:
    for path in modules(HTTP):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                assert attr not in EMITTING, (path.name, attr)  # nothing is ever logged
                assert attr not in MUTATING, (path.name, attr)  # no level/handler changes
                if attr == "getLogger":
                    assert path.name == SUPPRESSION_MODULE
                    assert node.args, "the root logger is never touched"
                    assert not (isinstance(node.args[0], ast.Constant)
                                and node.args[0].value in ("", "root")), "root logger"  # fmt: skip
                if attr == "addFilter":
                    assert path.name == SUPPRESSION_MODULE
                    assert [ast.unparse(a) for a in node.args] == ["SUPPRESSION_FILTER"]
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        assert target.attr not in {
                            "disabled",
                            "propagate",
                            "level",
                            "handlers",
                            "filters",
                        }, (path.name, target.attr)
        if path.name != SUPPRESSION_MODULE:
            assert "logging" not in imported(path), path.name


def test_the_filter_never_sees_request_data() -> None:
    """The filter's only input is the record and a boolean flag; nothing from a request
    is ever passed to logging (no logging call exists in the transport)."""
    from app.integrations.http import dependency_logging  # noqa: PLC0415

    tree = ast.parse(Path(dependency_logging.__file__).read_text())
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert [c.name for c in classes] == ["_SuppressInsideTransport"]
    methods = [n.name for n in classes[0].body if isinstance(n, ast.FunctionDef)]
    assert methods == ["filter"]
    assert not any(isinstance(n, ast.Assign) for n in classes[0].body)  # stores nothing


def test_every_logger_the_http_dependencies_use_is_covered() -> None:
    """Scans the installed httpx/httpcore sources for getLogger names: a new dependency
    logger must be added to DEPENDENCY_LOGGERS (fails loudly instead of leaking)."""
    import re  # noqa: PLC0415

    import httpcore  # noqa: PLC0415
    import httpx  # noqa: PLC0415

    from app.integrations.http.dependency_logging import DEPENDENCY_LOGGERS  # noqa: PLC0415

    found: set[str] = set()
    for package in (httpx, httpcore):
        assert package.__file__ is not None
        for path in Path(package.__file__).parent.rglob("*.py"):
            found.update(re.findall(r"getLogger\(\s*[\"']([^\"']+)[\"']", path.read_text()))
    assert found, "expected the dependencies to use named loggers"
    assert found <= set(DEPENDENCY_LOGGERS), found - set(DEPENDENCY_LOGGERS)
