"""Daily operations report boundaries: deterministic workflow, thin route, composition."""

import ast
import subprocess
import sys
from pathlib import Path

import app

APP_DIR = Path(app.__file__).parent
WORKFLOW_FILES = sorted((APP_DIR / "workflows").rglob("*.py"))
ROUTE = APP_DIR / "routes" / "operations_reports.py"
SERVICE = APP_DIR / "services" / "operations_reports.py"

WORKFLOW_ALLOWED_APP = (
    "app.workflows", "app.context.models", "app.governance", "app.commerce.domain",
    "app.integrations.commerce", "app.operations.actions", "app.services.operations_reports",
)  # fmt: skip
WORKFLOW_ALLOWED_ROOTS = {"collections", "datetime", "uuid", "zoneinfo", "typing"}


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_workflow_imports_only_contracts_domain_and_stdlib() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in WORKFLOW_FILES
        for m in imports(p)
        if not m.startswith(WORKFLOW_ALLOWED_APP) and m.split(".")[0] not in WORKFLOW_ALLOWED_ROOTS
    ]
    assert offenders == []
    for path in WORKFLOW_FILES:
        bad = [
            m
            for m in imports(path)
            if m.startswith(
                (
                    "fastapi",
                    "starlette",
                    "agno",
                    "sqlalchemy",
                    "redis",
                    "app.agents",
                    "app.commands",
                    "app.persistence",
                    "app.execution",
                    "app.routes",
                    "app.runtime",
                    "app.composition",
                    "app.integrations.commerce.mock",
                    "app.operations.tickets",
                    "app.application",
                )
            )
        ]
        assert bad == [], path.name  # fmt: skip


def test_workflow_never_invokes_a_model_agent_or_write() -> None:
    for path in WORKFLOW_FILES:
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for forbidden in ("Agent", "Model", "OperationsAgentRunner", "build_operations_agent",
                          "ExecutionCoordinator", "WriteCommandCoordinator", "arun", "run",
                          "create_ticket", "get_inventory", "CREATE_TICKET_ACTION"):  # fmt: skip
            assert forbidden not in names | attrs, (path.name, forbidden)


def test_importing_the_workflow_loads_no_runtime_model_or_persistence() -> None:
    # FastAPI is not checked at runtime: the existing ``app.context`` package __init__
    # (shared by governance and every layer using ``app.context.models``) exports the
    # HTTP dependencies. The workflow's own imports never name it (static test above).
    code = (
        "import sys, app.workflows; "
        "bad = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'agno', 'sqlalchemy', 'redis', 'openai', 'anthropic', 'psycopg'}); "
        "bad += sorted(m for m in sys.modules if m.startswith(('app.agents', 'app.persistence', "
        "'app.commands', 'app.routes', 'app.composition', 'app.integrations.commerce.mock', "
        "'app.runtime'))); print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_route_depends_only_on_the_service_contract() -> None:
    bad = [m for m in imports(ROUTE) if m.startswith((
        "app.workflows", "app.integrations", "app.governance.gate", "app.persistence",
        "app.agents", "app.runtime", "app.commands", "app.execution", "app.composition",
        "app.operations", "agno", "sqlalchemy"))]  # fmt: skip
    assert bad == []
    assert "app.services.operations_reports" in imports(ROUTE)
    service_bad = [m for m in imports(SERVICE) if m.startswith((
        "app.workflows", "app.integrations", "app.persistence", "app.agents", "app.runtime",
        "fastapi", "agno"))]  # fmt: skip
    assert service_bad == []


def test_composition_is_the_only_production_connector_of_the_workflow() -> None:
    importers = {
        str(p.relative_to(APP_DIR))
        for p in sorted(APP_DIR.rglob("*.py"))
        if (APP_DIR / "workflows") not in p.parents
        and any(m.startswith("app.workflows") for m in imports(p))
    }
    assert importers == {"composition/local_mock.py"}


def test_workflow_is_not_an_agent_tool_and_agent_tools_are_unchanged() -> None:
    from app.agents.operations_tools import build_operations_tools
    from app.governance import ActionCatalog, GovernanceGate
    from app.operations import OPERATIONS_ACTIONS
    from tests.agents.helpers import ops_stack

    for path in sorted((APP_DIR / "agents").rglob("*.py")):
        assert not [
            m
            for m in imports(path)
            if m.startswith(("app.workflows", "app.services.operations_reports"))
        ]
    stack = ops_stack([])
    gate = GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS))
    tools = build_operations_tools(commerce=stack.commerce, gate=gate,
                                   coordinator=stack.coordinator)  # fmt: skip
    assert sorted(t.__name__ for t in tools) == ["create_operational_ticket", "get_order",
                                             "get_order_shipments"]  # fmt: skip
