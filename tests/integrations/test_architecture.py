"""Integration boundary rules: one-way dependency, no frameworks, mock stays in mock/."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app.commerce
import app.integrations

INTEGRATIONS_DIR = Path(app.integrations.__file__).parent
# The secure outbound HTTP transport (Task 024) is network infrastructure for future
# adapters: it deliberately uses httpx and has its own boundary guards in
# tests/integration_http/test_architecture.py. Everything else stays framework-free.
HTTP_TRANSPORT_DIR = INTEGRATIONS_DIR / "http"
SOURCE_FILES = sorted(
    p for p in INTEGRATIONS_DIR.rglob("*.py") if HTTP_TRANSPORT_DIR not in p.parents
)
DOMAIN_FILES = sorted((Path(app.commerce.__file__).parent / "domain").rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {
    "__future__", "collections", "dataclasses", "datetime", "decimal", "enum", "typing",
    "uuid", "pydantic",
}  # fmt: skip
ALLOWED_APP_MODULES = ("app.integrations", "app.commerce.domain")
FORBIDDEN_NAMES = {
    "shopify", "woocommerce", "woo", "fulfly", "agno", "fastapi", "sqlalchemy", "httpx",
    "eval", "exec", "environ", "getenv", "importlib", "actor", "policy", "permission",
    "approval", "tool", "secret", "token", "password",
}  # fmt: skip
FORBIDDEN_MODULES = (
    "agno", "fastapi", "starlette", "sqlalchemy", "psycopg", "redis", "httpx", "openai",
    "anthropic", "requests", "socket_wrapper",
)  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_domain_never_imports_integrations() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in DOMAIN_FILES
        for m in imports(p)
        if m.startswith("app.integrations")
    ]
    assert offenders == []


def test_integrations_import_only_stdlib_pydantic_and_the_domain() -> None:
    offenders = [
        f"{p.relative_to(INTEGRATIONS_DIR)}: {m}"
        for p in SOURCE_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP_MODULES) and m.split(".")[0] not in ALLOWED_IMPORT_ROOTS
    ]
    assert offenders == []


def test_contract_layer_does_not_depend_on_the_mock() -> None:
    mock_dir = INTEGRATIONS_DIR / "commerce" / "mock"
    offenders = [
        f"{p.name}: {m}"
        for p in SOURCE_FILES
        if mock_dir not in p.parents
        for m in imports(p)
        if m.startswith("app.integrations.commerce.mock")
    ]
    assert offenders == []


def test_code_never_names_providers_frameworks_env_or_policy() -> None:
    """Identifiers only (docstrings and comments may explain the architecture)."""
    offenders = []
    for path in SOURCE_FILES:
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_")) | {token.string.lower()}
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")
    assert offenders == []


def test_importing_integrations_loads_no_framework_runtime_or_network_client() -> None:
    code = (
        "import sys; import app.integrations.commerce, app.integrations.commerce.mock; "
        "roots = {m.split('.')[0] for m in sys.modules}; "
        f"bad = sorted(roots & set({FORBIDDEN_MODULES!r})); "
        "bad += sorted(m for m in sys.modules if m.startswith("
        "('app.context', 'app.runtime', 'app.core', 'app.agents', 'app.company', 'app.main'))); "
        "print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=INTEGRATIONS_DIR.parents[1],
    )
    assert result.stdout.strip() == "[]"
