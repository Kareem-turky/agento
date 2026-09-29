"""Task 013 adds durable write commands but exposes NO HTTP write: the product HTTP
surface stays exactly the read-only Operations route and /health."""

import ast
from pathlib import Path

import app
from tests.routes import effective_api_routes

APP_DIR = Path(app.__file__).parent
HTTP_FILES = [APP_DIR / "main.py", *sorted((APP_DIR / "routes").rglob("*.py")),
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


def test_product_routes_are_unchanged(client) -> None:
    product = {
        (method, path)
        for method, path in effective_api_routes(client.app.routes)
        if path.startswith("/api/") or path == "/health"
    }
    assert product == {("POST", "/api/v1/operations/runs"), ("GET", "/health")}


def test_http_layer_does_not_reach_commands_or_persistence() -> None:
    offenders = [
        f"{p.relative_to(APP_DIR)}: {m}"
        for p in HTTP_FILES
        for m in imports(p)
        if m.startswith(("app.commands", "app.persistence"))
    ]
    assert offenders == []


def test_no_idempotency_header_or_write_mode_in_the_http_layer() -> None:
    names, strings = set(), []
    for path in HTTP_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name | ast.arg):
                names.add((node.id if isinstance(node, ast.Name) else node.arg).lower())
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value.lower())
    assert not {n for n in names if "idempotency" in n or "write_mode" in n}
    assert not [s for s in strings if "idempotency-key" in s or "write_mode" in s]
    assert "requested_write_actions" not in names  # the route never sets write intent


def test_operations_route_still_requests_no_writes() -> None:
    from app.agents.operations import OperationsAgentRunner

    source = Path(__import__("inspect").getsourcefile(OperationsAgentRunner)).read_text()
    assert "requested_write_actions=frozenset()" in source
