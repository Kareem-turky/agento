"""Architecture guards for Task 033 (Product Skills and Tasks)."""

import ast
import hashlib
from pathlib import Path

import app
from app.agent_management.capabilities import build_default_capability_graph

APP = Path(app.__file__).parent
ROOT = APP.parents[2]
CAPABILITY_MODULES = ("skills.py", "tasks.py", "capabilities.py")
CAPABILITY_PACKAGES = ("app.agent_management.skills", "app.agent_management.tasks",
                       "app.agent_management.capabilities")  # fmt: skip

# The trusted Operations implementation and the deterministic workflow, byte-for-byte as of
# Task 032 (main 53c56fd): Task 033 only adds metadata AROUND them.
PROTECTED = {
    "agents/operations.py": "0e56bb75a6e6decc9285cb8a4774f97696e25ef6359e4cca96d03a5a55a2cfcb",
    "agents/operations_tools.py":
        "21012d7629512df0d064848169c49f38132ddd6f38fa0dc5842ab6d7486501f4",
    "agents/operations_context.py":
        "f2066c40af944302a98512129528183c12929a0fd69efde5e0b6b12787e498d9",
    "workflows/operations_daily.py":
        "1b2e4b17a4c4494dd767e38503ef92daabd0e88e6bf41abc5ab5a095010c7b4a",
}  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_operations_agent_tools_and_workflow_are_unchanged() -> None:
    for relative, digest in PROTECTED.items():
        assert hashlib.sha256((APP / relative).read_bytes()).hexdigest() == digest, relative


def test_no_task_033_migration() -> None:
    # Task 033 added no migration; 0005 is Task 034's Workflow runtime state (no Skill or
    # Task table).
    versions = sorted(p.name for p in (ROOT / "apps/api/migrations/versions").glob("*.py"))
    assert versions == ["0001_create_write_commands.py", "0002_create_audit_events.py",
                        "0003_create_integration_connections.py",
                        "0004_create_agent_configurations.py",
                        "0005_create_workflow_runtime.py",
                        "0006_create_knowledge_context.py",
                        "0007_create_approvals.py"]  # fmt: skip
    for name in versions:
        text = (ROOT / "apps/api/migrations/versions" / name).read_text().lower()
        assert "skill" not in text, name


def test_skill_and_task_domain_is_pure_product_metadata() -> None:
    package = APP / "agent_management"
    for name in CAPABILITY_MODULES:
        source = (package / name).read_text()
        for module in imports(package / name):
            root = module.split(".")[0]
            # Task 034: the graph also validates Task -> Workflow references against the
            # static Product Workflow CATALOG (metadata only, never the engine).
            allowed = ("app.agent_management.", "app.workflow_management.catalog")
            stdlib = {"__future__", "collections", "dataclasses", "enum", "types", "typing"}
            assert root in stdlib | {"pydantic"} or module.startswith(allowed), (name, module)
        for word in ("agno", "sqlalchemy", "importlib", "entry_points", "__import__", "exec(",
                     "eval(", "open(", "glob(", "requests", "httpx"):  # fmt: skip
            assert word not in source, (name, word)


def test_governance_execution_and_runtime_never_consult_skills_or_tasks() -> None:
    """Skills and Tasks never become authorization: nothing that decides or runs an action
    imports them."""
    for layer in ("governance", "execution", "agents", "workflows", "operations", "runtime",
                  "services", "application", "integrations", "integration_management"):  # fmt: skip
        for path in (APP / layer).rglob("*.py"):
            for module in imports(path):
                assert not module.startswith(CAPABILITY_PACKAGES), path
    # Within Agent management, the authorization path (permissions, handlers, runtime gate)
    # does not read Skill/Task metadata either.
    for name in ("permissions.py", "handlers.py", "runtime.py", "actions.py"):
        for module in imports(APP / "agent_management" / name):
            assert not module.endswith((".skills", ".tasks", ".capabilities")), (name, module)


def test_skill_and_task_routes_are_get_only_with_no_executor() -> None:
    tree = ast.parse((APP / "routes" / "capabilities.py").read_text())
    methods = [d.func.attr for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
               for d in n.decorator_list if isinstance(d, ast.Call)
               and isinstance(d.func, ast.Attribute)]  # fmt: skip
    assert methods == ["get"] * 4
    source = (APP / "routes" / "capabilities.py").read_text()
    for word in ("/run", "schedule", "queue", "dispatch", "execute(", "coordinator"):
        assert word not in source.lower(), word


def test_capability_graph_is_valid_and_bounded_by_the_manifest() -> None:
    graph = build_default_capability_graph()  # raises CapabilityGraphError if inconsistent
    for agent in graph.agents.definitions():
        for task_id in agent.task_ids:
            task = graph.tasks.get(task_id)
            assert task is not None
            assert task.limits.max_tool_calls <= agent.manifest.tool_call_limit
            assert task.limits.allowed_write_actions <= agent.manifest.action_names


def test_the_composition_validates_the_graph_before_acquiring_resources() -> None:
    source = (APP / "composition" / "agents.py").read_text()
    assert source.index("build_default_capability_graph()") < source.index("create_product_engine(")
