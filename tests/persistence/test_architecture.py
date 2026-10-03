"""Persistence boundaries, migration ownership and API boot behaviour."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app
import app.persistence

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(app.__file__).parent
PERSISTENCE_FILES = sorted(Path(app.persistence.__file__).parent.rglob("*.py"))
PRODUCTION_FILES = sorted(APP_DIR.rglob("*.py")) + sorted(
    (ROOT / "apps" / "api" / "migrations").rglob("*.py")
)

ALLOWED_ROOTS = {"__future__", "collections", "datetime", "hmac", "typing", "uuid", "pydantic",
                 "sqlalchemy"}  # fmt: skip
# The command store contracts, the audit event contract PostgresAuditSink persists, and
# the contract vocabularies its CHECK constraints are derived from (pure models/enums).
# Task 031: the integration connection METADATA contract only (never the secret store,
# drivers, service, definitions with credentials, or any business integration).
ALLOWED_APP = ("app.persistence", "app.commands", "app.execution.audit",
               "app.execution.models", "app.governance.policy", "app.context.models",
               "app.integration_management.connections",
               "app.agent_management.configuration",
               # Task 034: the Workflow CONTROL-state contract, records and vocabularies.
               "app.workflow_management.contracts", "app.workflow_management.records",
               "app.workflow_management.state",
               # Task 035: the Knowledge repository contracts and pure domain modules
               # (never the service, handlers, routes or composition).
               "app.knowledge.contracts", "app.knowledge.documents",
               "app.knowledge.chunking", "app.knowledge.context",
               "app.knowledge.errors", "app.knowledge.limits",
               "app.knowledge.operating_context",
               # Task 036: the approval repository contract and pure domain modules
               # (never the service, handlers, routes or composition).
               "app.approval_management.errors", "app.approval_management.models",
               "app.approval_management.state", "app.execution.approvals",
               # Task 037: the conversation repository contract and pure domain modules
               # (never the ingress, read service, routes or composition).
               "app.conversations.contracts", "app.conversations.delivery",
               "app.conversations.errors", "app.conversations.models")  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_persistence_imports_only_sqlalchemy_and_the_command_contracts() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in PERSISTENCE_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP) and m.split(".")[0] not in ALLOWED_ROOTS
    ]
    assert offenders == []


def test_no_global_session_or_engine() -> None:
    for path in PERSISTENCE_FILES:
        tree = ast.parse(path.read_text())
        for node in tree.body:  # module-level statements only
            if isinstance(node, ast.Assign | ast.AnnAssign):
                value = ast.unparse(node.value) if node.value else ""
                for forbidden in ("create_async_engine", "create_engine", "sessionmaker",
                                  "Session(", "AsyncSession("):  # fmt: skip
                    assert forbidden not in value, f"{path.name}: {value}"
        names = {
            t.string
            for t in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
            if t.type == tokenize.NAME
        }
        assert "global" not in names and "lru_cache" not in names


def test_no_production_code_calls_create_all() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in PRODUCTION_FILES
        if "create_all" in p.read_text() or "drop_all" in p.read_text()
    ]
    assert offenders == []


def test_application_code_never_imports_alembic() -> None:
    offenders = [
        f"{p.relative_to(APP_DIR)}: {m}"
        for p in sorted(APP_DIR.rglob("*.py"))
        for m in imports(p)
        if m.split(".")[0] == "alembic"
    ]
    assert offenders == []


def test_api_boot_runs_no_migration_and_loads_no_product_persistence() -> None:
    code = (
        "import sys\n"
        "from agno.os.settings import AgnoAPISettings\n"
        "from app.config import Settings\n"
        "from app.main import create_app\n"
        "s = Settings(_env_file=None, environment='test',"
        " database_url='postgresql+psycopg://unit@127.0.0.1:1/unit')\n"
        "create_app(s, AgnoAPISettings(os_security_key='x' * 40))\n"
        "print('LOADED=' + ','.join(m for m in ('alembic', 'app.persistence', 'app.commands')"
        " if m in sys.modules))\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd="apps/api"
    )
    assert "LOADED=\n" in out.stdout


def code_strings(path: Path) -> list[str]:
    """String literals that are not docstrings."""
    tree = ast.parse(path.read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]  # fmt: skip


def test_persistence_does_not_touch_the_agno_schema() -> None:
    migration_files = sorted((ROOT / "apps" / "api" / "migrations").rglob("*.py"))
    literals = [s for p in PERSISTENCE_FILES + migration_files for s in code_strings(p)]
    assert not [s for s in literals if "agno" in s.lower()]
    assert not [s for s in literals if "cascade" in s.lower()]
    from app.persistence import PRODUCT_SCHEMA, write_commands

    assert PRODUCT_SCHEMA == "product" and write_commands.schema == "product"


def test_no_business_data_mirror_tables() -> None:
    """Task 031: Product persistence holds Product-owned state only. Source systems stay
    authoritative for business data, which is never mirrored into Product tables."""
    from app.persistence import product_metadata

    names = set(product_metadata.tables)
    # Task 034: Workflow execution CONTROL state only (no definition, no business data).
    assert names == {"product.write_commands", "product.audit_events",
                     "product.integration_connections",
                     "product.agent_configurations", "product.workflow_runs",
                     "product.workflow_step_runs", "product.workflow_events",
                     # Task 035: Product-owned operating model and operator-authored
                     # Knowledge text (never mirrored source-system business data).
                     "product.company_operating_model_versions",
                     "product.company_operating_model_current",
                     "product.knowledge_documents", "product.knowledge_document_versions",
                     "product.knowledge_chunks",
                     # Task 036: Product-owned approval decisions (no raw parameters).
                     "product.approval_requests", "product.approval_events",
                     # Task 037: the Product-owned canonical conversation transcript
                     # (no provider payload, CRM, attachment or Agent-memory table).
                     "product.conversations", "product.conversation_messages",
                     "product.message_delivery_events"}  # fmt: skip
    for path in PERSISTENCE_FILES:
        text = path.read_text()
        for forbidden in ("commerce_", "PostgresCommerceStore", "CommerceStoreReader",
                          "NativeCommerceAdapter", "secret_value", "get_secret_value"):  # fmt: skip
            assert forbidden not in text, (path.name, forbidden)
