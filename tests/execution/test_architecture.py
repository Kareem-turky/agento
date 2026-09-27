"""Execution is a governed pipeline: no agents, integrations, commerce, persistence or globals."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app.execution

EXECUTION_DIR = Path(app.execution.__file__).parent
SOURCE_FILES = sorted(EXECUTION_DIR.rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {
    "__future__", "collections", "datetime", "enum", "types", "typing", "uuid", "pydantic",
}  # fmt: skip
ALLOWED_APP_MODULES = ("app.execution", "app.governance", "app.context.models")
FORBIDDEN_NAMES = {
    "agno", "fastapi", "sqlalchemy", "httpx", "redis", "openai", "anthropic", "fulfly",
    "shopify", "woocommerce", "eval", "exec", "environ", "getenv", "importlib", "tenant",
    "approve", "approved", "bypass", "override", "token", "wildcard", "global",
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
        "models.py", "handlers.py", "audit.py", "coordinator.py", "errors.py",
    }  # fmt: skip


def test_imports_limited_to_stdlib_pydantic_governance_and_context_models() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in SOURCE_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP_MODULES) and m.split(".")[0] not in ALLOWED_IMPORT_ROOTS
    ]
    assert offenders == []


def test_reuses_existing_request_context_and_governance_models() -> None:
    from app.context.models import RequestContext
    from app.execution import coordinator, models
    from app.governance import PolicyDecision

    assert coordinator.RequestContext is RequestContext
    assert models.PolicyDecision is PolicyDecision
    source = "\n".join(p.read_text() for p in SOURCE_FILES)
    for duplicate in ("class RequestContext", "class ActorContext", "class PolicyDecision"):
        assert duplicate not in source


def test_no_forbidden_identifiers() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_")) | {token.string.lower()}
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")
    assert offenders == []


def test_no_module_level_mutable_state() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for node in ast.parse(path.read_text()).body:
            targets = (
                node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            )
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                continue  # the export list is conventional and never mutated
            if isinstance(node, ast.Assign | ast.AnnAssign):
                value = node.value
                if isinstance(value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp):
                    offenders.append(f"{path.name}:{node.lineno}")
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id in {"list", "dict", "set", "defaultdict", "deque"}:
                        offenders.append(f"{path.name}:{node.lineno}")
                    if value.func.id.endswith(("Registry", "Sink", "Coordinator")):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_importing_execution_loads_no_runtime_integration_or_persistence() -> None:
    code = (
        "import sys; import app.execution; "
        f"bad = sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})); "
        f"bad += sorted(m for m in sys.modules if m.startswith({FORBIDDEN_APP_MODULES!r})); "
        "print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=EXECUTION_DIR.parents[1],
    )
    assert result.stdout.strip() == "[]"


def test_governance_does_not_depend_on_execution() -> None:
    import app.governance

    for path in Path(app.governance.__file__).parent.rglob("*.py"):
        assert not any(m.startswith("app.execution") for m in imports(path))
