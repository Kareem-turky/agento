"""The Product HTTP surface, exactly (updated from the Task 013 "no HTTP write" guard).

- GET  /health
- POST /api/v1/operations/runs     Operations Agent, READ-ONLY
- POST /api/v1/operations/tickets  deterministic durable ticket command (the ONLY write)
- GET  /api/v1/operations/tickets/commands  read-only status of one ticket command

No generic command/action endpoint, no command listing, and no route reaches the
command layer or persistence directly.
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


INTEGRATION_ROUTES = {
    ("GET", "/api/v1/integrations/catalog"),
    ("GET", "/api/v1/integrations/connections"),
    ("POST", "/api/v1/integrations/connections"),
    ("GET", "/api/v1/integrations/connection"),
    ("PUT", "/api/v1/integrations/connection"),
    ("DELETE", "/api/v1/integrations/connection"),
    ("PUT", "/api/v1/integrations/connection/credentials"),
    ("POST", "/api/v1/integrations/connection/test"),
    ("POST", "/api/v1/integrations/connection/enable"),
    ("POST", "/api/v1/integrations/connection/disable"),
}
AGENT_ROUTES = {
    ("GET", "/api/v1/agents/catalog"),
    ("GET", "/api/v1/agents"),
    ("GET", "/api/v1/agents/agent"),
    ("POST", "/api/v1/agents/agent/enable"),
    ("POST", "/api/v1/agents/agent/disable"),
    ("DELETE", "/api/v1/agents/agent/configuration"),
}
CAPABILITY_ROUTES = {
    ("GET", "/api/v1/skills/catalog"),
    ("GET", "/api/v1/skills/skill"),
    ("GET", "/api/v1/tasks/catalog"),
    ("GET", "/api/v1/tasks/task"),
}
WORKFLOW_ROUTES = {
    ("GET", "/api/v1/workflows/catalog"),
    ("GET", "/api/v1/workflows/workflow"),
    ("GET", "/api/v1/workflows/runs"),
    ("GET", "/api/v1/workflows/run"),
}
KNOWLEDGE_ROUTES = {
    ("GET", "/api/v1/knowledge/operating-model"),
    ("GET", "/api/v1/knowledge/operating-model/versions"),
    ("GET", "/api/v1/knowledge/operating-model/version"),
    ("POST", "/api/v1/knowledge/operating-model/publish"),
    ("GET", "/api/v1/knowledge/documents"),
    ("GET", "/api/v1/knowledge/document"),
    ("GET", "/api/v1/knowledge/document/version"),
    ("POST", "/api/v1/knowledge/document/create"),
    ("POST", "/api/v1/knowledge/document/version"),
    ("POST", "/api/v1/knowledge/document/archive"),
    ("POST", "/api/v1/knowledge/query"),
}
# Task 036: human approvals. There is NO create route: approvals are created only
# internally, by governance (REQUIRE_APPROVAL) through the ExecutionCoordinator.
APPROVAL_ROUTES = {
    ("GET", "/api/v1/approvals"),
    ("GET", "/api/v1/approvals/approval"),
    ("POST", "/api/v1/approvals/approval/approve"),
    ("POST", "/api/v1/approvals/approval/reject"),
    ("POST", "/api/v1/approvals/approval/cancel"),
    ("POST", "/api/v1/approvals/approval/resume-workflow"),
}
# Task 037: read-only conversations. There is NO ingest/webhook and NO send route.
CONVERSATION_ROUTES = {
    ("GET", "/api/v1/conversations"),
    ("GET", "/api/v1/conversations/conversation"),
    ("GET", "/api/v1/conversations/messages"),
}
MANAGEMENT_PREFIXES = ("/api/v1/integrations/", "/api/v1/agents", "/api/v1/skills/",
                       "/api/v1/tasks/", "/api/v1/workflows/", "/api/v1/knowledge/",
                       "/api/v1/approvals", "/api/v1/conversations")  # fmt: skip


def test_product_routes_are_exactly_the_intended_surface(client) -> None:
    routes = effective_api_routes(client.app.routes)
    product = {
        (method, path)
        for method, path in routes
        if (path.startswith("/api/") and not path.startswith(MANAGEMENT_PREFIXES))
        or path == "/health"
    }
    # Task 031: the integration-management surface is exactly these fixed routes.
    assert {(m, p) for m, p in routes if p.startswith("/api/v1/integrations/")} == (
        INTEGRATION_ROUTES
    )
    # Task 032: the Agent-management surface is exactly these fixed routes.
    assert {(m, p) for m, p in routes if p.startswith("/api/v1/agents")} == AGENT_ROUTES
    # Task 033: Skill/Task inspection is exactly these READ-ONLY routes.
    capability = ("/api/v1/skills/", "/api/v1/tasks/")
    assert {(m, p) for m, p in routes if p.startswith(capability)} == CAPABILITY_ROUTES
    # Task 034: Workflow inspection is exactly these READ-ONLY routes (no run endpoint).
    workflows = {(m, p) for m, p in routes if p.startswith("/api/v1/workflows")}
    assert workflows == WORKFLOW_ROUTES
    # Task 035: Knowledge is exactly these fixed routes (no delete, upload or raw store).
    knowledge = {(m, p) for m, p in routes if p.startswith("/api/v1/knowledge")}
    assert knowledge == KNOWLEDGE_ROUTES
    # Task 036: approvals are exactly these fixed routes (list, read, decide, continue).
    approvals = {(m, p) for m, p in routes if p.startswith("/api/v1/approvals")}
    assert approvals == APPROVAL_ROUTES
    # Task 037: conversations are exactly these fixed READ-ONLY routes.
    conversations = {(m, p) for m, p in routes if p.startswith("/api/v1/conversations")}
    assert conversations == CONVERSATION_ROUTES
    assert not [p for _, p in approvals if "create" in p or "request" in p]
    assert product == {
        ("GET", "/health"),
        ("POST", "/api/v1/operations/runs"),
        ("GET", "/api/v1/operations/reports/daily"),
        ("POST", "/api/v1/operations/tickets"),
        ("GET", "/api/v1/operations/tickets/commands"),
    }


def test_no_generic_command_action_or_listing_endpoint(client) -> None:
    routes = effective_api_routes(client.app.routes)
    paths = {path for _, path in routes}
    # The only command path is the fixed, domain-specific ticket status read.
    assert [p for p in paths if "command" in p.lower()] == ["/api/v1/operations/tickets/commands"]
    assert [m for m, p in routes if p == "/api/v1/operations/tickets/commands"] == ["GET"]
    for word in ("/actions", "/execute", "idempot", "{"):
        assert not [p for p in paths if word in p.lower() and p.startswith("/api/")], word


def test_the_only_product_write_is_post_tickets(client) -> None:
    writes = {
        (method, path)
        for method, path in effective_api_routes(client.app.routes)
        if path.startswith("/api/")
        and method not in ("GET", "HEAD", "OPTIONS")
        and not path.startswith(MANAGEMENT_PREFIXES)
    }
    # /runs is a POST but read-only (asserted separately); tickets is the only BUSINESS
    # write. Integration-management (Task 031) and Agent-management (Task 032) writes manage
    # Product configuration only and are pinned by
    # test_product_routes_are_exactly_the_intended_surface.
    assert writes == {("POST", "/api/v1/operations/runs"), ("POST", "/api/v1/operations/tickets")}


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
