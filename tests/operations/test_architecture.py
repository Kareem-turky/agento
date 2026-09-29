"""Operations boundaries: governed writes only, contract-not-mock, no runtime coupling."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app
import app.operations

OPERATIONS_DIR = Path(app.operations.__file__).parent
APP_DIR = Path(app.__file__).parent
SOURCE_FILES = sorted(OPERATIONS_DIR.rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {"__future__", "collections", "enum", "typing", "uuid", "pydantic"}
ALLOWED_APP_MODULES = (
    "app.operations", "app.execution", "app.governance", "app.integrations.commerce",
    "app.commerce.domain",
)  # fmt: skip
FORBIDDEN_NAMES = {
    "agno", "fastapi", "sqlalchemy", "redis", "httpx", "fulfly", "shopify", "woocommerce",
    "tenant", "mock", "agent", "tool", "eval", "exec", "environ", "getenv", "importlib",
    "approve", "approved", "bypass", "override",
}  # fmt: skip
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
FORBIDDEN_MODULES = (
    "agno", "sqlalchemy", "psycopg", "redis", "httpx", "openai", "anthropic", "requests",
)  # fmt: skip
FORBIDDEN_APP_MODULES = (
    "app.integrations.commerce.mock", "app.runtime", "app.agents", "app.main", "app.company",
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


def test_imports_are_limited_to_the_governed_path_and_contracts() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in SOURCE_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP_MODULES) and m.split(".")[0] not in ALLOWED_IMPORT_ROOTS
    ]
    assert offenders == []


def test_operations_depend_on_the_contract_not_the_mock() -> None:
    offenders = [f"{p.name}: {m}" for p in SOURCE_FILES for m in imports(p) if ".mock" in m]
    assert offenders == []


def test_no_forbidden_identifiers() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_")) | {token.string.lower()}
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")
    assert offenders == []


def test_the_write_happens_only_inside_the_handler_execute() -> None:
    """No shortcut: create_ticket is called from CreateOperationalTicketHandler.execute
    only, so every ticket write passes through ExecutionCoordinator."""
    callers = []
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in [
                n for n in cls.body if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
            ]:
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr == "create_ticket":
                        callers.append(f"{cls.name}.{fn.name}")
        module_level = [
            n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for fn in module_level:
            assert fn.name.startswith("_"), f"public module function {fn.name} in {path.name}"
            assert not any(
                isinstance(n, ast.Attribute) and n.attr == "create_ticket" for n in ast.walk(fn)
            )
    assert callers == ["CreateOperationalTicketHandler.execute"]


def test_no_module_level_mutable_state() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for node in ast.parse(path.read_text()).body:
            value = getattr(node, "value", None)
            if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(
                value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


# Task 011: the Operations Agent modules are the one intended consumer of operations.
OPERATIONS_AGENT_MODULES = {
    "agents/operations.py", "agents/operations_context.py", "agents/operations_tools.py",
}  # fmt: skip


# The durable ticket command adapter (Task 014) names the action it submits; it takes
# only the trusted action definition, never a handler.
TICKET_COMMAND_ADAPTER = "application/operations_tickets.py"
# The read-only ticket status adapter (Task 015) names the action it may show.
TICKET_QUERY_ADAPTER = "application/operations_ticket_queries.py"


def test_operations_are_reached_only_by_the_operations_agent() -> None:
    """Only the Operations Agent and the ticket command/query adapters import app.operations:
    no HTTP endpoint, runtime hook or other agent reaches the ticket write directly."""
    importers = {
        str(p.relative_to(APP_DIR))
        for p in sorted(APP_DIR.rglob("*.py"))
        if OPERATIONS_DIR not in p.parents
        and any(m.startswith("app.operations") for m in imports(p))
    }
    adapters = {TICKET_COMMAND_ADAPTER, TICKET_QUERY_ADAPTER}
    assert importers <= OPERATIONS_AGENT_MODULES | adapters
    for adapter in adapters:
        adapter_imports = [m for m in imports(APP_DIR / adapter) if m.startswith("app.operations")]
        assert adapter_imports == ["app.operations.actions"], adapter


def test_runtime_and_http_do_not_wire_the_operations_agent() -> None:
    banned = ("app.operations", "app.agents.operations")
    for path in [APP_DIR / "main.py", *sorted((APP_DIR / "runtime").rglob("*.py"))]:
        bad = [m for m in imports(path) if m.startswith(banned)]
        assert bad == [], f"{path.relative_to(APP_DIR)}: {bad}"


def test_generic_layers_do_not_depend_on_operations_or_integrations() -> None:
    for package, banned in (
        ("execution", ("app.operations", "app.integrations", "app.commerce")),
        ("governance", ("app.operations", "app.integrations", "app.commerce", "app.execution")),
        ("commerce", ("app.operations", "app.integrations", "app.execution", "app.governance")),
        ("integrations", ("app.operations", "app.execution", "app.governance")),
    ):
        for path in (APP_DIR / package).rglob("*.py"):
            bad = [m for m in imports(path) if m.startswith(banned)]
            assert bad == [], f"{path.relative_to(APP_DIR)}: {bad}"


def test_importing_operations_loads_no_mock_runtime_or_persistence() -> None:
    code = (
        "import sys; import app.operations; "
        f"bad = sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})); "
        f"bad += sorted(m for m in sys.modules if m.startswith({FORBIDDEN_APP_MODULES!r})); "
        "print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=OPERATIONS_DIR.parents[1],
    )
    assert result.stdout.strip() == "[]"
