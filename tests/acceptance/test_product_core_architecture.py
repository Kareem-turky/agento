"""PRODUCT CORE ACCEPTANCE (Task 040): the static release-gate invariants.

Task 040 is ACCEPTANCE ONLY. These guards pin, against the Task 040 base
(82be2ec1896ac7a55ab57ace7d30570bc6a03557: Task 039 plus the pre-Core-Ready
Operations safe-validation hardening), that no production runtime code, migration,
dependency, Dockerfile or deployment runtime file changed; that the production catalogs
are exactly the Product definitions (integration catalog and messaging registry EMPTY);
that no TEST-ONLY action, Workflow, fake or acceptance hook reached production code; that
no injection route exists; and that CI runs the named "Product Core acceptance" gate.
"""

import ast
import hashlib
import importlib
import pkgutil
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api"
APP = API / "app"
WEB = ROOT / "apps" / "web"
ACCEPTANCE = ROOT / "tests" / "acceptance"
SUPPORT = ROOT / "tests" / "support" / "product_core.py"

# ----- 1. no production runtime change (Task 040 is acceptance only) --------------------------

PINNED_TREES = (
    APP, API / "migrations", WEB / "app", WEB / "components", WEB / "lib",
    ROOT / "deployments" / "template",
)  # fmt: skip
PINNED_FILES = (
    ROOT / "alembic.ini", ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "scripts" / "demo.sh",
    API / "Dockerfile", API / "scripts" / "hash_product_api_key.py", WEB / "Dockerfile",
    WEB / "package.json", WEB / "package-lock.json", WEB / "next.config.ts",
    WEB / "tsconfig.json",
)  # fmt: skip
# SHA-256 over "<relative path>\0<sha256 of the file>\n" of every pinned file, sorted by
# path, computed on a clean worktree of the Task 040 base 82be2ec (335 files). A change
# here is a production change: it needs its own reviewed task, never Task 040.
PRODUCTION_DIGEST = "be4fc7038373c6dd873812fe95a52c6895f2d2e2bcd793ad7d91d7a85df49c18"


def production_files() -> list[Path]:
    files = [p for tree in PINNED_TREES for p in tree.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]  # fmt: skip
    return sorted({*files, *PINNED_FILES})


def production_digest() -> str:
    digest = hashlib.sha256()
    for path in production_files():
        relative = path.relative_to(ROOT).as_posix()
        digest.update(f"{relative}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def test_no_production_runtime_dependency_or_deployment_file_changed() -> None:
    assert len(production_files()) > 300  # the pinned scope is the whole runtime
    assert production_digest() == PRODUCTION_DIGEST


def test_migration_history_is_exactly_0001_to_0008_with_a_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    names = sorted(p.name for p in (API / "migrations" / "versions").glob("*.py"))
    assert [n[:4] for n in names] == [f"{i:04d}" for i in range(1, 9)]
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == ["0008"]
    chain = [r.revision for r in script.walk_revisions()]
    assert chain == ["0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001"]


@pytest.mark.integration
def test_task_040_adds_no_table(migrated, engine) -> None:
    import sqlalchemy as sa

    with engine.connect() as connection:
        tables = sorted(connection.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'product'"
        )).scalars())  # fmt: skip
    assert tables == [
        "agent_configurations", "alembic_version", "approval_events", "approval_requests",
        "audit_events", "company_operating_model_current", "company_operating_model_versions",
        "conversation_messages", "conversations", "integration_connections",
        "knowledge_chunks", "knowledge_document_versions", "knowledge_documents",
        "message_delivery_events", "workflow_events", "workflow_runs", "workflow_step_runs",
        "write_commands",
    ]  # fmt: skip


def test_agno_pin_and_product_dependencies_are_unchanged() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '"agno[os,postgres,openai,anthropic]==3.0.11"' in pyproject
    web = (WEB / "package.json").read_text()
    assert "playwright" not in web.lower()  # the browser suite stays local-only


# ----- 2. production catalogs are exactly the Product definitions ------------------------------


def test_production_catalogs_and_registries() -> None:
    from app.agent_management.catalog import build_default_agent_catalog
    from app.agent_management.skills import build_default_skill_catalog
    from app.agent_management.tasks import build_default_task_catalog
    from app.composition.registry import build_default_backend_registry
    from app.governance import ActionRisk
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.operations import OPERATIONS_ACTIONS
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})
    assert sorted(s.skill_id for s in build_default_skill_catalog().definitions()) == [
        "operations.daily_analysis", "operations.order_inspection",
        "operations.ticket_escalation"]  # fmt: skip
    assert sorted(t.task_id for t in build_default_task_catalog().definitions()) == [
        "operations.analyze_daily",
        "operations.escalate_issue",
        "operations.inspect_order",
    ]
    assert build_default_workflow_catalog().workflow_ids == frozenset({"operations.daily_report"})
    integrations = build_default_integration_catalog()
    assert len(integrations) == 0
    assert len(build_default_messaging_registry(integrations)) == 0
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})
    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk is ActionRisk.LOW_RISK_WRITE  # no production approval requirement


def production_actions() -> list:
    """Every ActionDefinition reachable from any production module's globals."""
    import app
    from app.governance import ActionDefinition

    found = []
    for module in pkgutil.walk_packages(app.__path__, "app."):
        loaded = importlib.import_module(module.name)
        for value in vars(loaded).values():
            values = value if isinstance(value, tuple | list | frozenset | set) else (value,)
            found.extend(v for v in values if isinstance(v, ActionDefinition))
    return found


def test_no_test_only_or_risky_action_exists_in_production() -> None:
    from app.governance import ActionRisk

    actions = production_actions()
    names = {a.name for a in actions}
    assert "operations.ticket.create" in names and "system.read" in {
        a.required_permission for a in actions}  # fmt: skip
    assert not [n for n in names if n.startswith(("test.", "testing."))]
    assert {a.risk for a in actions} <= {ActionRisk.READ, ActionRisk.LOW_RISK_WRITE}


# ----- 3. no fake, hook, backdoor or injection route in production -----------------------------


def production_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text()) for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts]


def test_production_never_imports_tests_or_fakes() -> None:
    for path, source in production_sources():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                           else [a.name for a in node.names])  # fmt: skip
                for module in modules:
                    assert not module.startswith(("tests", "conftest")), (path, module)
                    assert "fake" not in module and "product_core" not in module, (path, module)


def test_no_acceptance_switch_or_backdoor_in_production() -> None:
    # Identifiers and setting names only (prose such as "acceptance criteria" is fine).
    pattern = re.compile(r"\b(TEST_MODE|ACCEPTANCE_\w+|\w*acceptance_mode|debug_backdoor|"
                         r"APP_TEST_\w*|is_acceptance|if acceptance)\b")  # fmt: skip
    for path, source in production_sources():
        assert not pattern.search(source), path


def test_no_product_route_injects_messages_approvals_workflow_state_or_executes() -> None:
    from agno.os.settings import AgnoAPISettings
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from tests.conftest import TEST_OS_SECURITY_KEY, UNREACHABLE_DATABASE_URL
    from tests.routes import effective_api_routes

    app = create_app(Settings(_env_file=None, environment="test",
                              database_url=UNREACHABLE_DATABASE_URL),
                     AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY))  # fmt: skip
    with TestClient(app):
        product = sorted({(m, p) for m, p in effective_api_routes(app.routes)
                          if p.startswith("/api/v1/")})  # fmt: skip
    assert product
    for method, path in product:
        for word in ("ingest", "inject", "webhook", "send", "execute", "inbound", "seed",
                     "acceptance", "test-only", "debug"):  # fmt: skip
            assert word not in path, (method, path)
    writes = {path for method, path in product if method in {"POST", "PUT", "PATCH"}}
    assert not [
        p
        for p in writes
        if p.startswith("/api/v1/approvals") and p.endswith(("/create", "/approvals"))
    ]  # no Approval create route
    assert not [p for p in writes if p.startswith(("/api/v1/workflows",
                                                   "/api/v1/conversations"))]  # fmt: skip


# ----- 4. acceptance stays provider-free and TEST-ONLY -----------------------------------------

_REAL_PROVIDERS = ("shopify", "woocommerce", "whatsapp", "twilio", "google ads",
                   "".join(("ful", "fly")))  # fmt: skip


def acceptance_sources() -> list[tuple[Path, str]]:
    """Task 040 acceptance code (this guard, which only lists the prohibited names, aside)."""
    files = [*sorted(ACCEPTANCE.glob("test_product_core_*.py")), SUPPORT]
    return [(p, p.read_text()) for p in files if p.name != Path(__file__).name]


def test_acceptance_code_models_no_real_provider() -> None:
    for path, source in acceptance_sources():
        code = "\n".join(line for line in source.lower().splitlines()
                         if not line.lstrip().startswith("#"))  # fmt: skip
        for provider in _REAL_PROVIDERS:
            assert provider not in code, (path.name, provider)
        assert not re.search(r"\bmeta\b", code), path.name  # no social-platform provider


def test_the_acceptance_harness_injects_only_through_existing_seams() -> None:
    source = SUPPORT.read_text()
    patched = set(re.findall(r'monkeypatch\.setattr\((\w+), "(\w+)"', source))
    assert patched == {
        ("bootstrap", "build_integration_management"), ("bootstrap", "build_conversations"),
        ("bootstrap", "build_deployment_observability"), ("local_mock", "MockTicketDesk"),
        ("local_mock", "DailyOperationsWorkflow"),
    }  # fmt: skip
    for seam in ("catalog=self.catalog", "stream=self.log"):
        assert seam in source


# ----- 5. the explicit CI gate and the acceptance record ---------------------------------------


def test_ci_runs_the_named_product_core_acceptance_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["backend"]["steps"]
    names = [s.get("name", "") for s in steps]
    gate = names.index("Product Core acceptance")
    full = next(i for i, s in enumerate(steps) if "pytest" in s.get("run", "")
                and "tests/acceptance" not in s.get("run", ""))  # fmt: skip
    migrate = next(i for i, s in enumerate(steps) if "alembic" in s.get("run", ""))
    assert migrate < full < gate
    step = steps[gate]
    assert "uv run pytest tests/acceptance" in step["run"]
    assert step["env"]["REQUIRE_INTEGRATION_TESTS"] == "1"  # PostgreSQL missing: FAIL
    # APP_DATABASE_URL comes from the job environment (.env.example, the CI database).
    assert workflow["jobs"]["backend"]["env"]["REQUIRE_INTEGRATION_TESTS"] == "1"


def test_the_acceptance_record_states_the_core_ready_boundaries() -> None:
    raw = (ROOT / "docs" / "PRODUCT_CORE_ACCEPTANCE.md").read_text()
    record = " ".join(raw.replace("**", "").split())  # prose may wrap across lines
    decision = (ROOT / "docs" / "PRODUCT_CORE_READY.md").read_text()
    for statement in (
        "Production IntegrationCatalog remains empty",
        "proof of contract composability, NOT a shipped integration",
        "refuses to start without a reviewed real business backend",
        "reverse proxy", "TLS termination", "Audit", "Observability", "Real Integrations",
    ):  # fmt: skip
        assert statement in record, statement
    for area in ("Auth", "Permissions", "Policy", "Agent management", "Skills/Tasks",
                 "Operations", "Workflow", "WriteCommand", "Verification", "Audit",
                 "Integration management", "Knowledge", "Approvals", "Conversations",
                 "Control Center", "System/readiness", "Observability", "Backup/restore",
                 "Deployment packaging"):  # fmt: skip
        assert f"\n| {area} |" in raw, area
    # The first-pass Operations validation finding is resolved on the baseline, not open.
    assert "## Resolved before final acceptance" in raw and "## Findings" not in raw
    assert "82be2ec1896ac7a55ab57ace7d30570bc6a03557" in raw
    assert "82be2ec1896ac7a55ab57ace7d30570bc6a03557" in decision
    # Self-finalizing decision: an objective merge gate, never a static mutable status.
    flat = " ".join(decision.replace("**", "").split())
    assert "Status: candidate" not in decision and "Status: final" not in decision
    for concept in ("Status rule:", "candidate until this Task 040 commit is on `main`",
                    "push-to-main CI on that exact commit is green", "final thereafter",
                    "Before merge", "Task 040 acceptance candidate",
                    "push-to-main CI on that exact merge commit is green",
                    "Agento Product Core Ready (provider-free)",
                    "No follow-up documentation commit"):  # fmt: skip
        assert concept in flat, concept
    assert "Product Core acceptance" in decision and "provider-free" in decision
