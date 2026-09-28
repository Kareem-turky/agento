"""Operations Agent boundaries: Agno + product contracts only, governed paths only."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app
import app.agents

AGENTS_DIR = Path(app.agents.__file__).parent
APP_DIR = Path(app.__file__).parent
OPERATIONS_AGENT_FILES = sorted(AGENTS_DIR.glob("operations*.py"))

ALLOWED_IMPORT_ROOTS = {
    "__future__", "collections", "dataclasses", "enum", "typing", "uuid", "pydantic", "agno",
}  # fmt: skip
ALLOWED_APP_MODULES = (
    "app.agents.operations", "app.context.models", "app.governance", "app.execution",
    "app.operations", "app.commerce.domain",
)  # fmt: skip
ALLOWED_INTEGRATION_MODULES = ("app.integrations.commerce",)
FORBIDDEN_MODULES = (
    "sqlalchemy", "psycopg", "redis", "httpx", "requests", "openai", "anthropic",
)  # fmt: skip
FORBIDDEN_APP_MODULES = (
    "app.integrations.commerce.mock", "app.runtime", "app.main", "app.core", "app.company",
)  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def names(path: Path) -> set[str]:
    return {
        t.string
        for t in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
        if t.type == tokenize.NAME
    }


def test_operations_agent_files_exist() -> None:
    assert {p.name for p in OPERATIONS_AGENT_FILES} == {
        "operations.py", "operations_context.py", "operations_tools.py",
    }  # fmt: skip


def test_imports_are_agno_and_product_contracts_only() -> None:
    offenders = []
    for path in OPERATIONS_AGENT_FILES:
        for module in imports(path):
            if (
                module.startswith(ALLOWED_APP_MODULES)
                or module.split(".")[0] in ALLOWED_IMPORT_ROOTS
            ):
                continue
            if module in ALLOWED_INTEGRATION_MODULES:  # the contract package itself, not mock
                continue
            offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_agno_usage_is_the_model_abstraction_and_native_runtime() -> None:
    agno_imports = {m for p in OPERATIONS_AGENT_FILES for m in imports(p) if m.startswith("agno")}
    assert agno_imports <= {"agno.agent", "agno.models.base", "agno.run", "agno.run.agent"}
    for path in OPERATIONS_AGENT_FILES:
        assert not any(
            m.startswith("agno.models.") and m != "agno.models.base" for m in imports(path)
        )


FORBIDDEN_WRITE_NAMES = (
    "TicketingIntegration", "CreateOperationalTicketHandler", "MockTicketingAdapter",
    "MockCommerceAdapter", "requires_confirmation",
)  # fmt: skip


def test_no_direct_integration_write_or_handler_call() -> None:
    """The only write path is tool -> ExecutionCoordinator.run -> handler -> integration."""
    for path in OPERATIONS_AGENT_FILES:
        tree = ast.parse(path.read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "create_ticket" not in attrs, path.name
        assert "execute" not in attrs and "verify" not in attrs, path.name
        used = names(path)
        for forbidden in FORBIDDEN_WRITE_NAMES:
            assert forbidden not in used, f"{path.name}: {forbidden}"
    tools = (AGENTS_DIR / "operations_tools.py").read_text()
    assert "coordinator.run(" in tools


def test_only_the_three_product_tools_exist() -> None:
    tree = ast.parse((AGENTS_DIR / "operations_tools.py").read_text())
    builder = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "build_operations_tools"
    )
    returned = next(n for n in ast.walk(builder) if isinstance(n, ast.Return))
    assert isinstance(returned.value, ast.List)
    assert [e.id for e in returned.value.elts if isinstance(e, ast.Name)] == [
        "get_order", "get_order_shipments", "create_operational_ticket",
    ]  # fmt: skip


def test_generic_core_does_not_depend_on_agents_or_business_layers() -> None:
    for package, banned in (
        ("execution", ("app.agents", "app.operations", "app.integrations", "app.commerce")),
        (
            "governance",
            ("app.agents", "app.execution", "app.operations", "app.integrations", "app.commerce"),
        ),  # fmt: skip
        (
            "commerce",
            ("app.agents", "app.operations", "app.execution", "app.governance", "app.integrations"),
        ),  # fmt: skip
        ("operations", ("app.agents",)),
        ("integrations", ("app.agents", "app.operations", "app.execution", "app.governance")),
    ):
        for path in (APP_DIR / package).rglob("*.py"):
            bad = [m for m in imports(path) if m.startswith(banned)]
            assert bad == [], f"{path.relative_to(APP_DIR)}: {bad}"


def test_operations_agent_is_not_registered_or_routed() -> None:
    for path in [APP_DIR / "main.py", *sorted((APP_DIR / "runtime").rglob("*.py"))]:
        text = path.read_text()
        assert "app.agents.operations" not in text and "build_operations_agent" not in text
        assert not any(
            m.startswith(("app.agents.operations", "app.operations")) for m in imports(path)
        )
    others = [p for p in APP_DIR.rglob("*.py") if p not in OPERATIONS_AGENT_FILES]
    for path in others:
        assert not any(m.startswith("app.agents.operations") for m in imports(path)), path


def test_importing_the_agent_loads_no_mock_runtime_persistence_or_provider_sdk() -> None:
    code = (
        "import sys; import app.agents.operations; "
        f"bad = sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})); "
        f"bad += sorted(m for m in sys.modules if m.startswith({FORBIDDEN_APP_MODULES!r})); "
        "print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=AGENTS_DIR.parents[1],
    )
    assert result.stdout.strip() == "[]"
