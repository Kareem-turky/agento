"""The Product HTTP surface, exactly (updated from the Task 013 "no HTTP write" guard).

- GET  /health
- POST /api/v1/operations/runs     Operations Agent, READ-ONLY
- POST /api/v1/operations/tickets  deterministic durable ticket command (the ONLY write)

No generic command/action endpoint, no command status endpoint, and no route reaches
the command layer or persistence directly.
"""

import ast
from pathlib import Path

import app
from tests.routes import effective_api_routes

APP_DIR = Path(app.__file__).parent
RUNS_ROUTE = APP_DIR / "routes" / "operations.py"
TICKETS_ROUTE = APP_DIR / "routes" / "operations_tickets.py"
MAIN = APP_DIR / "main.py"
HTTP_FILES = [MAIN, *sorted((APP_DIR / "routes").rglob("*.py")),
              *sorted((APP_DIR / "services").rglob("*.py")),
              *sorted((APP_DIR / "runtime").rglob("*.py"))]  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def identifiers_and_strings(path: Path) -> tuple[set[str], list[str]]:
    names, strings = set(), []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg.lower())
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value.lower())
    return names, strings


def test_product_routes_are_exactly_the_intended_surface(client) -> None:
    product = {
        (method, path)
        for method, path in effective_api_routes(client.app.routes)
        if path.startswith("/api/") or path == "/health"
    }
    assert product == {
        ("GET", "/health"),
        ("POST", "/api/v1/operations/runs"),
        ("POST", "/api/v1/operations/tickets"),
    }


def test_no_generic_command_action_or_status_endpoint(client) -> None:
    paths = {path for _, path in effective_api_routes(client.app.routes)}
    for word in ("command", "/actions", "/execute", "idempot"):
        assert not [p for p in paths if word in p.lower()], word


def test_http_layer_never_reaches_commands_persistence_or_execution() -> None:
    forbidden = ("app.commands", "app.persistence", "app.execution", "app.application",
                 "app.operations", "app.integrations")  # fmt: skip
    offenders = [
        f"{p.relative_to(APP_DIR)}: {m}"
        for p in HTTP_FILES
        for m in imports(p)
        if m.startswith(forbidden)
    ]
    assert offenders == []


def test_the_runs_route_stays_read_only_and_knows_no_idempotency() -> None:
    names, strings = identifiers_and_strings(RUNS_ROUTE)
    assert not {n for n in names if "idempotency" in n or "write_mode" in n}
    assert not [s for s in strings if "idempotency" in s or "write_mode" in s]
    assert "requested_write_actions" not in names
    assert not [m for m in imports(RUNS_ROUTE) if "operations_tickets" in m]
    calls = {
        n.func.attr
        for n in ast.walk(ast.parse(RUNS_ROUTE.read_text()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "run_product" in calls and "create_ticket" not in calls


def test_only_the_ticket_route_handles_the_idempotency_key_header() -> None:
    holders = [
        p.relative_to(APP_DIR)
        for p in HTTP_FILES
        if any("idempotency-key" in s for s in identifiers_and_strings(p)[1])
    ]
    assert [str(h) for h in holders] == ["routes/operations_tickets.py"]
    names, _ = identifiers_and_strings(TICKETS_ROUTE)
    assert not {"write_mode", "requested_write_actions", "action_name"} & names


def test_operations_route_still_requests_no_writes() -> None:
    from app.agents.operations import OperationsAgentRunner

    source = Path(__import__("inspect").getsourcefile(OperationsAgentRunner)).read_text()
    assert "requested_write_actions=frozenset()" in source
