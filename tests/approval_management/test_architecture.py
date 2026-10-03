"""Architecture guards for Task 036 (Governance & Human Approval Productization v1)."""

import ast
import hashlib
import re
from pathlib import Path

import app
import app.approval_management

APP = Path(app.__file__).parent
ROOT = APP.parents[2]
PACKAGE = Path(app.approval_management.__file__).parent
VERSIONS = ROOT / "apps/api/migrations/versions"
MIGRATION = VERSIONS / "0007_create_approvals.py"
APPROVAL_FILES = [*sorted(PACKAGE.glob("*.py")), APP / "execution" / "approvals.py",
                  APP / "persistence" / "approvals.py", APP / "routes" / "approvals.py",
                  APP / "composition" / "approvals.py", MIGRATION]  # fmt: skip
PROVIDERS = ("shopify", "woocommerce", "whatsapp", "bosta", "shipblu", "meta ads",
             "google ads", "salla", "f" + "ulfly")  # fmt: skip
# Byte-for-byte as on main before Task 036 (e022e48): approvals change none of them.
PROTECTED = {
    "agents/operations.py": "0e56bb75a6e6decc9285cb8a4774f97696e25ef6359e4cca96d03a5a55a2cfcb",
    "agents/operations_context.py":
        "f2066c40af944302a98512129528183c12929a0fd69efde5e0b6b12787e498d9",
    "agents/operations_tools.py":
        "21012d7629512df0d064848169c49f38132ddd6f38fa0dc5842ab6d7486501f4",
    "workflows/operations_daily_platform.py":
        "736963930573a5cf10d35e03dce5d432c191d4360079a2a9a3544d2dd692e4a0",
    "workflow_management/catalog.py":
        "c429500b534a7835dd8b00cfa216825fa8355253fccea9b7198297686ba2f7ad",
    "workflow_management/definitions.py":
        "cf637677296016e1cefbd7737af2aede68c6191ae08e6492a8ee439de11a778f",
    "agent_management/skills.py":
        "81bb2fbc59de82b3086253913eaf12f142cc30acd4c09ad83e56c565301bdf24",
    "agent_management/tasks.py":
        "bf8916db9c080f6f931d3ee626451eb89e49556c8d115cf36c22fd8b9ddd4ccd",
    "agent_management/catalog.py":
        "6619f3f6d179f07260c9341b385622079b055f02e568209c40c14cf68157a911",
    "agent_management/capabilities.py":
        "b0bf1d9b3a094a44e06ed54e9fb0cd5ed9f5492a5cee201ce52cf8d2e6f548b2",
    "operations/actions.py": "1aa48fbced01c9625f6803dd59bf33900dd686d73436e964058182350a6d317b",
    "governance/gate.py": "78e779bbd0eaf3a853a6439b95d3d22cbad940b802b1b8db718660ea3bb6ad24",
    "governance/permissions.py":
        "e539cfd63f02b731706cd2ed87cf012d658747cba70d4d82f2f1bd67d94c6d78",
    "governance/policy.py": "f2326816bcd83e7e5d765fedc39e930357b397d130a7647f678ad51a73433c41",
    "integration_management/catalog.py":
        "57cd4ba732536d5f7377d753316056be3ce74926438a91712d7039761c421b6b",
    "composition/registry.py":
        "bb6e276c66d0588c4004ee47d3e707b2c8d938821923b3c755d808a566fc5a78",
}  # fmt: skip
MIGRATIONS_0001_TO_0006 = {
    "0001_create_write_commands.py":
        "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2",
    "0002_create_audit_events.py":
        "b0512d7f743451349f22f77d5b7e079e37ce49c00cba77f05cbd1c861e449ca9",
    "0003_create_integration_connections.py":
        "e5aab3a40f51835f8a3c4606eb521fa53fc772627cd05853551fc7d72fad4ad5",
    "0004_create_agent_configurations.py":
        "22436e6a09a51a5bc031f3eece6994ed5dd9c02eb8f650731c2f254675a01786",
    "0005_create_workflow_runtime.py":
        "3984d4a3f3a3f9318511489da49fd0035b4d7246065b05e940f5223292bee8dc",
    "0006_create_knowledge_context.py":
        "ca308ddd3f2f63f8f3b171a5643da2d6832da7c4c712c2f3338174cc53a59b2a",
}  # fmt: skip
# Every Knowledge source file (Task 035), as one digest: approvals change none of them.
KNOWLEDGE_DIGEST = "ebd8928176e8568e515f2b9c1f0c3b07749e8716f5baac8e92ffaba56972c832"


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def code(path: Path) -> str:
    """Source without comments and docstrings."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):  # fmt: skip
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined]
    return ast.unparse(tree)


def test_package_layout() -> None:
    assert {p.name for p in PACKAGE.glob("*.py")} == {
        "__init__.py", "state.py", "fingerprint.py", "errors.py", "models.py",
        "contracts.py", "broker.py", "actions.py", "permissions.py", "handlers.py",
        "service.py",
    }  # fmt: skip


def test_the_domain_is_runtime_provider_and_transport_independent() -> None:
    allowed_roots = {"collections", "dataclasses", "datetime", "enum", "hashlib", "json",
                     "typing", "unicodedata", "uuid", "pydantic"}  # fmt: skip
    allowed_app = ("app.approval_management", "app.context.models", "app.governance",
                   "app.execution", "app.observability.contracts",
                   "app.workflow_management.errors")  # fmt: skip
    for path in sorted(PACKAGE.glob("*.py")):
        for module in imports(path):
            assert module.split(".")[0] in allowed_roots or module.startswith(allowed_app), (
                path.name, module)  # fmt: skip


def test_no_agno_hitl_cloud_model_or_network_anywhere_in_approvals() -> None:
    forbidden = ("agno", "openai", "anthropic", "app.agents", "app.runtime", "app.integrations",
                 "app.workflow_management.engine", "httpx", "requests", "socket", "urllib",
                 "aiohttp", "smtplib")  # fmt: skip
    for path in APPROVAL_FILES:
        for module in imports(path):
            assert not module.startswith(forbidden), (path.name, module)
        lower = path.read_text().lower()
        for word in ("agno cloud", "control plane", "os.agno.com", "agno_api_key",
                     "requires_confirmation", "user_input_required", "external_execution",
                     "continue_run"):  # fmt: skip
            assert word not in lower, (path.name, word)
        for provider in PROVIDERS:
            assert provider not in lower, (path.name, provider)


def test_no_dynamic_code_and_no_background_worker() -> None:
    for path in APPROVAL_FILES:
        text = code(path)
        for call in ("importlib", "pkgutil", "runpy", "__import__(", "exec(", "eval(",
                     "pickle", "marshal", "subprocess", "os.system", "urlopen", "http://",
                     "https://", "create_task", "ensure_future", "threading", "Thread(",
                     "asyncio.sleep", "time.sleep", "scheduler", "apscheduler", "celery",
                     "while True"):  # fmt: skip
            assert call not in text, (path.name, call)


def test_no_public_create_and_no_raw_parameter_storage() -> None:
    routes = code(APP / "routes" / "approvals.py")
    for word in ("ApprovalBroker", "ApprovalSubject", "ProductApprovalBroker", "repository"):
        assert word not in routes, word
    assert not [p for p in re.findall(r'"(/api/v1/approvals[^"]*)"', routes) if "create" in p]
    assert len(re.findall(r"@router\.(get|post|put|patch|delete)\(", routes)) == 6
    assert not re.findall(r"@router\.(put|patch|delete)\(", routes)
    for path in (MIGRATION, APP / "persistence" / "approvals.py"):
        columns = re.findall(r"sa\.Column\(\s*\"([a-z_]+)\"", path.read_text())
        assert "subject_fingerprint" in columns and "summary" in columns, path.name
        for column in columns:
            for word in ("parameter", "payload", "input", "raw", "body", "prompt", "secret",
                         "token", "password", "credential", "idempotency"):  # fmt: skip
                assert word not in column, (path.name, column)


def test_approvals_are_created_only_by_governance_through_the_coordinator() -> None:
    callers = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
               if re.search(r"\.request\(\s*(subject|ApprovalSubject)", p.read_text())}  # fmt: skip
    assert callers == {"execution/coordinator.py"}
    coordinator = code(APP / "execution" / "coordinator.py")
    assert "PolicyOutcome.REQUIRE_APPROVAL" in coordinator
    # The broker is reached only through the ExecutionCoordinator (plus composition).
    users = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
             if "ProductApprovalBroker" in code(p) and PACKAGE not in p.parents}  # fmt: skip
    assert users == {"composition/local_mock.py"}


def test_ticket_risk_and_production_actions_are_unchanged() -> None:
    from app.approval_management.actions import APPROVAL_ACTIONS
    from app.governance import ActionRisk
    from app.operations import OPERATIONS_ACTIONS

    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk is ActionRisk.LOW_RISK_WRITE
    # Task 036 adds NO real MEDIUM/HIGH business action: the only MEDIUM/HIGH actions are
    # TEST-ONLY definitions under tests/support.
    for path in APP.rglob("*.py"):
        if path.parent.name == "governance" and path.name in ("actions.py", "policy.py"):
            continue
        if path.parent == PACKAGE and path.name == "models.py":
            continue  # APPROVAL_RISKS: the risks a request may have (a validation rule)
        assert not re.search(r"ActionRisk\.(MEDIUM|HIGH)_RISK\b", code(path)), path.name
    assert {a.risk for a in APPROVAL_ACTIONS} <= {ActionRisk.READ, ActionRisk.LOW_RISK_WRITE}


def test_protected_files_catalogs_knowledge_and_migrations_are_unchanged() -> None:
    for relative, digest in PROTECTED.items():
        assert hashlib.sha256((APP / relative).read_bytes()).hexdigest() == digest, relative
    files = [*sorted((APP / "knowledge").glob("*.py")), APP / "persistence/knowledge.py",
             APP / "routes/knowledge.py", APP / "composition/knowledge.py"]  # fmt: skip
    knowledge = hashlib.sha256()
    for path in files:
        knowledge.update(str(path.relative_to(APP)).encode() + b"\0" + path.read_bytes() + b"\0")
    assert knowledge.hexdigest() == KNOWLEDGE_DIGEST
    for name, digest in MIGRATIONS_0001_TO_0006.items():
        assert hashlib.sha256((VERSIONS / name).read_bytes()).hexdigest() == digest, name

    from app.agent_management.catalog import build_default_agent_catalog
    from app.composition.registry import build_default_backend_registry
    from app.integration_management.catalog import build_default_integration_catalog
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})
    assert build_default_workflow_catalog().workflow_ids == frozenset({"operations.daily_report"})
    assert len(build_default_integration_catalog()) == 0
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})


def test_0007_is_on_0006_and_keeps_data_on_downgrade() -> None:
    names = sorted(p.name for p in VERSIONS.glob("*.py"))
    # Task 037 adds 0008 (conversations) on top of 0007.
    assert names == [*sorted(MIGRATIONS_0001_TO_0006), "0007_create_approvals.py",
                     "0008_create_conversations.py"]  # fmt: skip
    text = MIGRATION.read_text()
    assert 'revision: str = "0007"' in text and 'down_revision: str | None = "0006"' in text
    downgrade = code(MIGRATION).split("def downgrade")[1]
    for word in ("CASCADE", "DROP SCHEMA", "DELETE FROM", "TRUNCATE", "drop_table('write",
                 "drop_table('audit", "drop_table('workflow"):  # fmt: skip
        assert word not in downgrade, word
    assert "NOT VALID" in code(MIGRATION)  # narrower CHECKs never delete newer history


def test_no_secret_or_credential_storage_in_approvals() -> None:
    for path in APPROVAL_FILES:
        lower = path.read_text().lower()
        for word in ("secretstr", "get_secret_value", "password", "api_key", "credential_",
                     "token="):  # fmt: skip
            assert word not in lower, (path.name, word)


def test_no_agent_or_tool_can_decide_an_approval() -> None:
    for path in [*(APP / "agents").rglob("*.py"), *(APP / "runtime").rglob("*.py")]:
        text = path.read_text()
        for word in ("app.approval_management", "ApprovalService", "approvals.decide"):
            assert word not in text, (path.name, word)
    permissions = code(PACKAGE / "permissions.py")
    assert "system_agent" in permissions
