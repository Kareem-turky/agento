"""Product Operations route boundaries: read-only, product-auth, no runtime coupling."""

import ast
import asyncio
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

import app
from app.agents.operations import OperationsRunFailedError
from app.governance import ActionScope
from app.routes.operations import (
    OPERATIONS_RUNS_PATH,
    OperationsRunRequest,
    OperationsRunResponse,
)
from app.runtime.agentos import attach_agent_os
from app.runtime.errors import RuntimeConfigurationError
from tests.agents.helpers import COMPANY, STORE, ops_stack, request
from tests.support.scripted_tool_model import Reply

APP_DIR = Path(app.__file__).parent
ROUTE = APP_DIR / "routes" / "operations.py"
SERVICE = APP_DIR / "services" / "operations.py"
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


def test_route_path_and_schemas() -> None:
    assert OPERATIONS_RUNS_PATH == "/api/v1/operations/runs"
    assert set(OperationsRunRequest.model_fields) == {"message", "store_id"}
    assert OperationsRunRequest.model_config["extra"] == "forbid"
    assert set(OperationsRunResponse.model_fields) == {"request_id", "message"}
    assert OperationsRunResponse.model_config["extra"] == "forbid"


def test_route_imports_only_web_context_governance_and_the_service_contract() -> None:
    allowed_roots = {"typing", "uuid", "fastapi", "pydantic"}
    allowed_app = ("app.context", "app.governance", "app.services.operations")
    bad = [m for m in imports(ROUTE) if m.split(".")[0] not in allowed_roots
           and not m.startswith(allowed_app)]  # fmt: skip
    assert bad == []


def test_service_contract_is_runtime_independent() -> None:
    allowed = ("typing", "pydantic", "app.context.models", "app.governance")
    assert [m for m in imports(SERVICE) if not m.startswith(allowed)] == []


def test_route_cannot_enable_writes_or_reach_execution() -> None:
    used = names(ROUTE)
    for forbidden in (
        "CREATE_TICKET_ACTION", "requested_write_actions", "ExecutionCoordinator",
        "TicketingIntegration", "CommerceIntegration", "CreateOperationalTicketHandler",
        "OperationsAgentRunner", "RunContext", "Agent", "RunOutput", "Model", "AgentOS",
        "TrustedOperationsRunContext", "session_id", "user_id", "allow_write",
    ):  # fmt: skip
        assert forbidden not in used, forbidden
    calls = {
        n.func.attr
        for n in ast.walk(ast.parse(ROUTE.read_text()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "run_product" in calls and "run" not in calls


def test_importing_the_route_loads_no_runtime_or_business_internals() -> None:
    code = (
        "import sys; import app.routes.operations; "
        "roots = {m.split('.')[0] for m in sys.modules}; "
        "bad = sorted(roots & {'agno', 'sqlalchemy', 'redis', 'httpx', 'openai', 'anthropic'}); "
        "bad += sorted(m for m in sys.modules if m.startswith(('app.agents', 'app.operations', "
        "'app.integrations', 'app.execution', 'app.commerce', 'app.runtime'))); print(bad)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_main_has_no_mock_or_agent_wiring() -> None:
    for module in imports(MAIN):
        assert not module.startswith(
            ("app.integrations", "app.agents", "app.operations", "app.execution", "tests")
        ), module
    text = MAIN.read_text()
    assert "Mock" not in text and "build_operations_agent" not in text


def test_run_product_is_always_read_only() -> None:
    tree = ast.parse((APP_DIR / "agents" / "operations.py").read_text())
    method = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_product"
    )
    (call,) = [
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
    ]
    (kw,) = [k for k in call.keywords if k.arg == "requested_write_actions"]
    assert isinstance(kw.value, ast.Call) and ast.unparse(kw.value) == "frozenset()"
    assert [a.arg for a in method.args.args] == ["self", "request", "scope", "message"]


def test_run_product_returns_only_text_and_fails_closed() -> None:
    s = ops_stack([Reply("final text")])
    scope = ActionScope(company_id=COMPANY, store_id=STORE)
    result = asyncio.run(s.runner.run_product(request(), scope, "hi"))
    assert result.model_dump() == {"message": "final text"}

    s2 = ops_stack([Reply("x")])

    async def broken(*args, **kwargs):
        raise ConnectionError("provider detail")

    s2.model.ainvoke = broken  # type: ignore[method-assign]
    with pytest.raises(OperationsRunFailedError) as info:
        asyncio.run(s2.runner.run_product(request(), scope, "hi"))
    assert "provider detail" not in str(info.value)


def test_agentos_exemption_takes_exact_paths_only(settings, runtime_settings) -> None:
    from fastapi import FastAPI

    with pytest.raises(RuntimeConfigurationError):
        attach_agent_os(FastAPI(), settings, runtime_settings, product_route_paths=("/api/v1/*",))
    source = MAIN.read_text()
    # Exactly the four operations paths plus the exact integration-management paths (Task
    # 031, a tuple of fixed strings); no pattern, no prefix.
    compact = "".join(source.split())
    assert (
        "product_route_paths=(OPERATIONS_RUNS_PATH,OPERATIONS_DAILY_REPORT_PATH,"
        "OPERATIONS_TICKETS_PATH,OPERATIONS_TICKET_COMMANDS_PATH,*INTEGRATIONS_PATHS,"
        "*AGENTS_PATHS,*CAPABILITIES_PATHS,*WORKFLOWS_PATHS,*KNOWLEDGE_PATHS,"
        "*APPROVALS_PATHS,)" in compact
    )
    from app.routes.agents import AGENTS_PATHS

    assert all(p.startswith("/api/v1/agents") and not any(c in p for c in "*?[{")
               for p in AGENTS_PATHS)  # fmt: skip
    from app.routes.integrations import INTEGRATIONS_PATHS

    assert all(p.startswith("/api/v1/integrations/") and not any(c in p for c in "*?[{")
               for p in INTEGRATIONS_PATHS)  # fmt: skip
