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


def test_transport_imports_only_stdlib_httpx_and_itself() -> None:
    for path in modules(HTTP):
        for name in imported(path):
            if name.startswith("app."):
                assert name.startswith("app.integrations.http"), (path.name, name)
            else:
                assert name.split(".")[0] in ALLOWED_ROOTS, (path.name, name)


@pytest.mark.parametrize(
    "forbidden",
    ["fastapi", "starlette", "agno", "app.agents", "app.workflows", "app.governance",
     "app.execution", "app.commands", "app.persistence", "redis", "app.observability",
     "app.composition", "app.integrations.commerce", "opentelemetry", "logging",
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
             "getenv", "environ", "settings", "logger"}  # fmt: skip
    for path in modules(HTTP):
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME:
                parts = set(token.string.lower().split("_")) | {token.string.lower()}
                assert not parts & words, (path.name, token.string)
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
