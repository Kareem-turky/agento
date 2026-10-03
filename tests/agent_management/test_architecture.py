"""Architecture guards for Task 032 (Product Agent management)."""

import ast
from pathlib import Path

import app
import app.agent_management
from app.agent_management import OPERATIONS_AGENT_DEFINITION, build_default_agent_catalog

APP = Path(app.__file__).parent
PACKAGE = Path(app.agent_management.__file__).parent
DOMAIN = ("definitions.py", "catalog.py", "configuration.py", "state.py", "skills.py",
          "tasks.py", "capabilities.py")  # fmt: skip
STDLIB = {"__future__", "collections", "dataclasses", "datetime", "enum", "types", "typing"}
PROVIDERS = ("shopify", "woocommerce", "whatsapp", "bosta", "shipblu", "meta ads",
             "google ads", "f" + "ulfly")  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_package_layout() -> None:
    assert {p.name for p in PACKAGE.glob("*.py")} == {
        "__init__.py", *DOMAIN, "actions.py", "handlers.py", "permissions.py", "service.py",
        "runtime.py",
    }  # fmt: skip
    assert len(DOMAIN) == 7


def test_domain_depends_only_on_the_standard_library_and_pydantic() -> None:
    for name in DOMAIN:
        for module in imports(PACKAGE / name):
            # Task 034: capabilities.py validates Task -> Workflow references against the
            # static Product Workflow catalog (metadata only).
            assert module.split(".")[0] in STDLIB | {"pydantic"} or module.startswith(
                ("app.agent_management.", "app.workflow_management.catalog")
            ), (name, module)


def test_no_dynamic_loading_agno_frameworks_or_providers() -> None:
    forbidden = ("importlib", "pkgutil", "runpy", "agno", "fastapi", "sqlalchemy", "httpx",
                 "requests", "socket", "app.agents", "app.integrations", "app.composition",
                 "app.integration_management", "app.persistence", "app.runtime")  # fmt: skip
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        for module in imports(path):
            assert not module.startswith(forbidden), (path.name, module)
        for call in ("__import__(", "entry_points", "exec(", "eval(", "import_module",
                     "getattr(module", "globals()["):  # fmt: skip
            assert call not in text, (path.name, call)
        for provider in PROVIDERS:
            assert provider not in text.lower(), (path.name, provider)


def test_definitions_name_no_provider_class_or_import_path() -> None:
    for definition in build_default_agent_catalog().definitions():
        dumped = definition.model_dump_json().lower()
        for provider in PROVIDERS:
            assert provider not in dumped, provider
        for word in ("app.", ":", "/", "class", "module", "instruction", "prompt", "model_id"):
            assert word not in definition.agent_id
        assert "app.agents" not in dumped and "import" not in dumped
        for requirement in definition.manifest.requirements:
            assert requirement.split(".")[0] in {"commerce", "operations", "model"}, requirement


def test_operations_definition_matches_the_trusted_implementation() -> None:
    """The definition DESCRIBES the existing Operations Agent; it does not rebuild it."""
    from app.agents.operations import OPERATIONS_AGENT_ID, OPERATIONS_TOOL_CALL_LIMIT
    from app.operations import OPERATIONS_ACTIONS

    assert OPERATIONS_AGENT_DEFINITION.agent_id == OPERATIONS_AGENT_ID
    assert OPERATIONS_AGENT_DEFINITION.manifest.tool_call_limit == OPERATIONS_TOOL_CALL_LIMIT
    tree = ast.parse((APP / "agents" / "operations_tools.py").read_text())
    builder = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "build_operations_tools"
    )
    returned = next(n for n in ast.walk(builder) if isinstance(n, ast.Return))
    tools = [e.id for e in ast.walk(returned.value) if isinstance(e, ast.Name)]  # type: ignore[arg-type]
    declared = [t.tool_id for t in OPERATIONS_AGENT_DEFINITION.manifest.tools]
    assert sorted(declared) == sorted(t for t in tools if t in declared)
    assert set(declared) <= set(tools)
    assert OPERATIONS_AGENT_DEFINITION.manifest.action_names <= {a.name for a in OPERATIONS_ACTIONS}


def test_runtime_infrastructure_agents_are_not_product_agents() -> None:
    from app.agents.generic_reasoning import GENERIC_REASONING_AGENT_ID

    assert GENERIC_REASONING_AGENT_ID not in build_default_agent_catalog()


def test_agents_and_business_layers_never_import_agent_management() -> None:
    for layer in ("agents", "workflows", "operations", "integrations", "commerce", "services",
                  "application", "integration_management", "runtime"):  # fmt: skip
        for path in (APP / layer).rglob("*.py"):
            for module in imports(path):
                assert not module.startswith("app.agent_management"), (layer, path.name)


def test_agent_management_is_wired_only_through_composition_main_and_routes() -> None:
    users = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
             if PACKAGE not in p.parents
             and any(m.startswith("app.agent_management") for m in imports(p))}  # fmt: skip
    assert users == {"main.py", "routes/agents.py", "routes/capabilities.py",
                     "composition/agents.py",
                     # Task 035: only the installed Agent ids (capability intent check).
                     "composition/knowledge.py",
                     "persistence/agent_configurations.py"}  # fmt: skip


def test_agentos_never_registers_product_business_agents() -> None:
    source = (APP / "runtime" / "agentos.py").read_text() + (
        APP / "runtime" / "components.py"
    ).read_text()
    assert "agent_management" not in source and "build_operations_agent" not in source
