"""Governance is pure decision logic: no execution, integrations, persistence or runtime."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app.governance

GOVERNANCE_DIR = Path(app.governance.__file__).parent
SOURCE_FILES = sorted(GOVERNANCE_DIR.rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {"__future__", "collections", "enum", "types", "typing", "pydantic"}
ALLOWED_APP_MODULES = ("app.governance", "app.context.models")
FORBIDDEN_NAMES = {
    "agno", "fastapi", "sqlalchemy", "httpx", "redis", "fulfly", "shopify", "woocommerce",
    "eval", "exec", "environ", "getenv", "importlib", "execute", "integration", "integrations",
    "tenant", "wildcard",
}  # fmt: skip
FORBIDDEN_MODULES = (
    "agno", "sqlalchemy", "psycopg", "redis", "httpx", "openai", "anthropic",
)  # fmt: skip
FORBIDDEN_APP_MODULES = (
    "app.integrations", "app.commerce", "app.company", "app.runtime", "app.agents", "app.main",
    "app.core",
)  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_package_modules() -> None:
    assert {p.name for p in SOURCE_FILES} >= {
        "actions.py", "permissions.py", "policy.py", "gate.py",
    }  # fmt: skip


def test_imports_limited_to_stdlib_pydantic_and_the_actor_model() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in SOURCE_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP_MODULES) and m.split(".")[0] not in ALLOWED_IMPORT_ROOTS
    ]
    assert offenders == []


def test_reuses_the_existing_actor_context() -> None:
    from app.context.models import ActorContext
    from app.governance import permissions

    assert permissions.ActorContext is ActorContext
    source = "\n".join(p.read_text() for p in SOURCE_FILES)
    assert "class ActorContext" not in source and "class Actor(" not in source


def test_permission_evaluator_does_not_depend_on_catalog_or_intent() -> None:
    source = (GOVERNANCE_DIR / "permissions.py").read_text()
    assert "ActionCatalog" not in source and "ActionIntent" not in source


def test_no_forbidden_identifiers() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_")) | {token.string.lower()}
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")
    assert offenders == []


def test_importing_governance_loads_no_runtime_integration_or_persistence() -> None:
    code = (
        "import sys; import app.governance; "
        f"bad = sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})); "
        f"bad += sorted(m for m in sys.modules if m.startswith({FORBIDDEN_APP_MODULES!r})); "
        "print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=GOVERNANCE_DIR.parents[1],
    )
    assert result.stdout.strip() == "[]"
