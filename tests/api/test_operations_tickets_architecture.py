"""Boundaries of the ticket write: route -> pure service contract <- application adapter."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app

APP_DIR = Path(app.__file__).parent
ROUTE = APP_DIR / "routes" / "operations_tickets.py"
SERVICE = APP_DIR / "services" / "operations_tickets.py"
ADAPTER_FILES = sorted((APP_DIR / "application").rglob("*.py"))
MAIN = APP_DIR / "main.py"


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


def loaded_after_import(module: str) -> set[str]:
    code = f"import sys, {module}; print('\\n'.join(sorted(sys.modules)))"
    out = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    return set(out.stdout.split())


def test_route_imports_only_web_context_governance_and_the_ticket_contract() -> None:
    allowed_roots = {"typing", "uuid", "fastapi", "pydantic"}
    allowed_app = ("app.context", "app.governance", "app.services.operations_tickets",
                   "app.routes.validation")  # fmt: skip
    bad = [m for m in imports(ROUTE) if m.split(".")[0] not in allowed_roots
           and not m.startswith(allowed_app)]  # fmt: skip
    assert bad == []


def test_route_cannot_choose_the_action_or_reach_execution() -> None:
    used = names(ROUTE)
    for forbidden in (
        "CREATE_TICKET_ACTION", "ActionIntent", "action_name", "WriteCommandCoordinator",
        "ExecutionCoordinator", "CreateOperationalTicketHandler", "TicketingIntegration",
        "PostgresWriteCommandStore", "requested_write_actions", "OperationsAgentRunner",
        "Agent", "RunContext", "session_id", "user_id", "allow_write",
    ):  # fmt: skip
        assert forbidden not in used, forbidden
    calls = {
        n.func.attr
        for n in ast.walk(ast.parse(ROUTE.read_text()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "create_ticket" in calls and not {"submit", "run", "run_product"} & calls


def test_importing_the_route_loads_no_command_persistence_or_runtime_code() -> None:
    loaded = loaded_after_import("app.routes.operations_tickets")
    roots = {m.split(".")[0] for m in loaded}
    assert not roots & {"agno", "sqlalchemy", "psycopg", "redis", "alembic", "openai",
                        "anthropic"}  # fmt: skip
    bad = sorted(m for m in loaded if m.startswith((
        "app.commands", "app.persistence", "app.execution", "app.operations",
        "app.integrations", "app.agents", "app.application", "app.runtime",
    )))  # fmt: skip
    assert bad == []


def test_service_contract_is_infrastructure_independent() -> None:
    allowed = ("enum", "typing", "uuid", "pydantic", "app.context.models", "app.governance")
    assert [m for m in imports(SERVICE) if not m.startswith(allowed)] == []


def test_application_adapter_dependencies() -> None:
    allowed_roots = {"uuid", "typing", "pydantic"}
    allowed_app = ("app.commands", "app.context.models", "app.governance",
                   "app.operations.actions", "app.services.operations_tickets",
                   "app.services")  # fmt: skip
    bad = [
        f"{p.name}: {m}"
        for p in ADAPTER_FILES
        for m in imports(p)
        if m.split(".")[0] not in allowed_roots and not m.startswith(allowed_app)
    ]
    assert bad == []
    for path in ADAPTER_FILES:
        used = names(path)
        for forbidden in ("CreateOperationalTicketHandler", "TicketingIntegration",
                          "PostgresWriteCommandStore", "ExecutionCoordinator", "Mock"):  # fmt: skip
            assert forbidden not in used, f"{path.name}: {forbidden}"


def test_application_adapter_loads_no_persistence_web_or_agent_runtime() -> None:
    loaded = loaded_after_import("app.application.operations_tickets")
    roots = {m.split(".")[0] for m in loaded}
    assert not roots & {"agno", "sqlalchemy", "psycopg", "redis", "alembic"}
    assert not [m for m in loaded if m.startswith(("app.persistence", "app.agents",
                                                   "app.runtime", "app.routes"))]  # fmt: skip


def test_the_adapter_fixes_the_action_server_side() -> None:
    source = (APP_DIR / "application" / "operations_tickets.py").read_text()
    assert "ActionIntent(name=CREATE_TICKET_ACTION.name)" in source
    from app.operations.actions import CREATE_TICKET_ACTION

    assert CREATE_TICKET_ACTION.name == "operations.ticket.create"


def test_main_composes_nothing_and_registers_no_operations_agent() -> None:
    for module in imports(MAIN):
        assert not module.startswith(
            ("app.commands", "app.persistence", "app.application", "app.execution",
             "app.operations", "app.integrations", "app.agents", "tests")
        ), module  # fmt: skip
    used = names(MAIN)
    forbidden_names = (
        "WriteCommandCoordinator", "PostgresWriteCommandStore", "ExecutionCoordinator",
        "WriteCommandTicketService", "MockTicketDesk",
    )  # fmt: skip
    for forbidden in forbidden_names:
        assert forbidden not in used


QUERY_ADAPTER = APP_DIR / "application" / "operations_ticket_queries.py"


def test_query_adapter_dependencies() -> None:
    allowed_roots = {"uuid", "typing", "pydantic"}
    allowed_app = ("app.commands", "app.context.models", "app.operations.actions",
                   "app.services.operations_tickets")  # fmt: skip
    bad = [m for m in imports(QUERY_ADAPTER) if m.split(".")[0] not in allowed_roots
           and not m.startswith(allowed_app)]  # fmt: skip
    assert bad == []
    used = names(QUERY_ADAPTER)
    for forbidden in (
        "WriteCommandCoordinator", "WriteCommandStore", "ExecutionCoordinator",
        "GovernanceGate", "CreateOperationalTicketHandler", "TicketingIntegration",
        "PostgresWriteCommandStore", "claim", "complete", "submit", "Mock",
    ):  # fmt: skip
        assert forbidden not in used, forbidden


def test_query_adapter_loads_no_persistence_web_or_agent_runtime() -> None:
    loaded = loaded_after_import("app.application.operations_ticket_queries")
    roots = {m.split(".")[0] for m in loaded}
    assert not roots & {"agno", "sqlalchemy", "psycopg", "redis", "alembic"}
    assert not [m for m in loaded if m.startswith(("app.persistence", "app.agents",
                                                   "app.runtime", "app.routes"))]  # fmt: skip


def test_get_route_reaches_only_the_query_contract() -> None:
    tree = ast.parse(ROUTE.read_text())
    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "get_operations_ticket_command"]  # fmt: skip
    calls = {n.func.attr for n in ast.walk(func)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}  # fmt: skip
    assert "get_command" in calls
    assert not calls & {"create_ticket", "submit", "run", "claim", "complete", "get_for_actor"}
    used = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}
    assert not used & {"ActionScope", "IDEMPOTENCY_KEY_HEADER", "body"}
