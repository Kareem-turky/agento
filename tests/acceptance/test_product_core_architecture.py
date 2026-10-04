"""PRODUCT REGRESSION ACCEPTANCE: static architecture invariants (Task 040, Task 041).

Task 040 pinned the exact Product Core release. Task 041 keeps those facts as HISTORICAL
evidence (docs/PRODUCT_CORE_RELEASE_BASELINE.md, plus the unchanged release decision and
acceptance record, all checked here byte for byte). It no longer requires the evolving
``main`` to equal the release. The ENDURING invariants stay active and fail closed:
- migrations stay a valid, single-headed chain that keeps 0001-0008 byte-identical;
- the existing Product catalog entries cannot silently disappear;
- no TEST-ONLY action, fake, acceptance switch or injection route reaches production;
- the provider boundary holds;
- CI runs the named "Product regression acceptance" gate.
See docs/PRODUCT_REGRESSION_ACCEPTANCE.md.
"""

import ast
import hashlib
import importlib
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api"
APP = API / "app"
WEB = ROOT / "apps" / "web"
DOCS = ROOT / "docs"
ACCEPTANCE = ROOT / "tests" / "acceptance"
SUPPORT = ROOT / "tests" / "support" / "product_core.py"
MIGRATIONS = API / "migrations" / "versions"

# ----- 1. the Product Core release: historical evidence, exactly preserved ----------------------

# The release production-digest algorithm (kept so the record can be re-verified on a
# checkout of the release commit; see docs/PRODUCT_CORE_RELEASE_BASELINE.md). It is NOT
# applied to the evolving main.
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

RELEASE = {
    "release_commit": "a69fb36ffe5b50957425e7a40df89aeaa63c8166",
    "release_tree": "ea84aa981beedb24f5c6f5e96b603bd9cdaaf270",
    "task_040_base": "82be2ec1896ac7a55ab57ace7d30570bc6a03557",
    "push_main_ci_run": "37158103014",
    "production_digest": "be4fc7038373c6dd873812fe95a52c6895f2d2e2bcd793ad7d91d7a85df49c18",
    "production_file_count": "335",
    "migrations": "0001-0008",
    "migration_head": "0008",
    "agents": "{operations}",
    "skills": "{operations.order_inspection, operations.daily_analysis, "
              "operations.ticket_escalation}",
    "tasks": "{operations.inspect_order, operations.analyze_daily, operations.escalate_issue}",
    "workflows": "{operations.daily_report}",
    "integration_catalog": "empty",
    "messaging_registry": "empty",
    "backend_registry": "{mock}",
    "ticket_create_risk": "LOW_RISK_WRITE",
    "agno_pin": "agno[os,postgres,openai,anthropic]==3.0.11",
}  # fmt: skip
# The release decision and acceptance record describe the release commit; never rewritten.
RELEASE_DOCUMENTS = {
    "PRODUCT_CORE_ACCEPTANCE": "7c3a6e1fea6abf7563421701c9e862d111d57b00e3b0f0783aee913ee5e799be",
    "PRODUCT_CORE_READY": "7d33de791723095657c408662cd251b58ea3908c8cf42ea996a04ba6fe44264f",
}
# Migrations 0001-0008 exactly as released: later tasks add revisions, never rewrite these.
RELEASE_MIGRATIONS = {
    "0001": "66a1f14e4fcae5f6c17802cda7499591c9cb00693dbd5244cc3f464ea89297f2",
    "0002": "b0512d7f743451349f22f77d5b7e079e37ce49c00cba77f05cbd1c861e449ca9",
    "0003": "e5aab3a40f51835f8a3c4606eb521fa53fc772627cd05853551fc7d72fad4ad5",
    "0004": "22436e6a09a51a5bc031f3eece6994ed5dd9c02eb8f650731c2f254675a01786",
    "0005": "3984d4a3f3a3f9318511489da49fd0035b4d7246065b05e940f5223292bee8dc",
    "0006": "ca308ddd3f2f63f8f3b171a5643da2d6832da7c4c712c2f3338174cc53a59b2a",
    "0007": "0bab564c73a9bcc9a1e24568c1627e707b813f37fcd172d41a48df26b280fde4",
    "0008": "baf48dc6ba3b47d75fb65b9da0ccc3c3b15a7a66d1d71d9490155c8a2aa04ab2",
}  # fmt: skip
RELEASE_TABLES = (
    "agent_configurations", "alembic_version", "approval_events", "approval_requests",
    "audit_events", "company_operating_model_current", "company_operating_model_versions",
    "conversation_messages", "conversations", "integration_connections", "knowledge_chunks",
    "knowledge_document_versions", "knowledge_documents", "message_delivery_events",
    "workflow_events", "workflow_runs", "workflow_step_runs", "write_commands",
)  # fmt: skip


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


def baseline_record() -> dict[str, str]:
    text = (DOCS / "PRODUCT_CORE_RELEASE_BASELINE.md").read_text()
    block = text.split("```text\n", 1)[1].split("```", 1)[0]
    return dict(
        (key.strip(), value.strip())
        for key, value in (line.split(":", 1) for line in block.splitlines() if line.strip())
    )


def test_the_release_baseline_record_keeps_the_exact_release_facts() -> None:
    text = (DOCS / "PRODUCT_CORE_RELEASE_BASELINE.md").read_text()
    assert "THIS IS HISTORICAL RELEASE EVIDENCE." in text
    assert "not** a constraint that future `main`" in text
    assert baseline_record() == RELEASE
    # The digest algorithm the record refers to is still here (re-verifiable on a release
    # checkout), but it is deliberately NOT asserted against the evolving main.
    assert len(production_files()) > 300


def test_the_release_documents_are_unchanged_historical_records() -> None:
    for name, sha in RELEASE_DOCUMENTS.items():
        assert hashlib.sha256((DOCS / f"{name}.md").read_bytes()).hexdigest() == sha, name


# ----- 2. migrations: an extensible, single-headed chain that keeps 0001-0008 ------------------


def test_release_migrations_are_kept_byte_identical() -> None:
    for revision, sha in RELEASE_MIGRATIONS.items():
        (path,) = MIGRATIONS.glob(f"{revision}_*.py")  # exactly one file per revision
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, revision


def test_the_migration_chain_is_single_headed_and_descends_from_the_release() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, heads  # one head: no branch, no orphan revision
    chain = [r.revision for r in script.walk_revisions()]
    for revision in script.walk_revisions():
        down = revision.down_revision
        assert down is None or isinstance(down, str), revision.revision  # no merge points
        assert (down is None) == (revision.revision == "0001"), revision.revision
    assert chain[-8:] == ["0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001"]
    # Every revision added after the release descends linearly from 0008.
    assert chain[0] == heads[0] and len(chain) == len(set(chain))
    files = {p.name[:4] for p in MIGRATIONS.glob("*.py")}
    assert {f"{i:04d}" for i in range(1, 9)} <= files


@pytest.mark.integration
def test_every_release_table_still_exists(migrated, engine) -> None:
    import sqlalchemy as sa

    with engine.connect() as connection:
        tables = set(connection.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'product'"
        )).scalars())  # fmt: skip
    assert set(RELEASE_TABLES) <= tables  # data of Tasks 001-040 never silently disappears


def test_agno_stays_an_exact_pin() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    pins = re.findall(r'"(agno\[[a-z,]+\])([^"]*)"', pyproject)
    assert len(pins) == 1 and re.fullmatch(r"==\d+\.\d+\.\d+", pins[0][1]), pins


# ----- 3. the existing Product definitions never silently disappear --------------------------


def test_existing_product_definitions_remain_and_extensions_are_allowed() -> None:
    from app.agent_management.catalog import build_default_agent_catalog
    from app.agent_management.skills import build_default_skill_catalog
    from app.agent_management.tasks import build_default_task_catalog
    from app.composition.registry import build_default_backend_registry
    from app.governance import ActionRisk
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.operations import OPERATIONS_ACTIONS
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert "operations" in build_default_agent_catalog().agent_ids
    assert {"operations.order_inspection", "operations.daily_analysis",
            "operations.ticket_escalation"} <= {
        s.skill_id for s in build_default_skill_catalog().definitions()}  # fmt: skip
    assert {"operations.inspect_order", "operations.analyze_daily",
            "operations.escalate_issue"} <= {
        t.task_id for t in build_default_task_catalog().definitions()}  # fmt: skip
    workflows = build_default_workflow_catalog().workflow_ids
    assert "operations.daily_report" in workflows
    assert not [w for w in workflows if w.startswith(("test", "example"))]
    # Local, test and acceptance installations run on the deterministic mock backend.
    assert "mock" in build_default_backend_registry().backend_ids
    # Integrations may be installed by reviewed tasks; TEST-ONLY definitions never are.
    integrations = build_default_integration_catalog()
    installed = {d.integration_id for d in integrations.definitions()}
    assert not [i for i in installed if i.startswith(("example-", "test"))]
    messaging = build_default_messaging_registry(integrations)
    assert messaging.integration_ids <= installed  # an adapter only for an installed one
    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk is ActionRisk.LOW_RISK_WRITE  # changed only by a reviewed task


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


def test_no_test_only_action_exists_in_production() -> None:
    actions = production_actions()
    names = {a.name for a in actions}
    assert "operations.ticket.create" in names and "system.read" in {
        a.required_permission for a in actions}  # fmt: skip
    assert not [n for n in names if n.startswith(("test.", "testing."))]


# ----- 4. no fake, hook, backdoor or injection route in production -----------------------------


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


# ----- 5. the provider boundary (real adapters are allowed, behind it) -------------------------

INTEGRATIONS = APP / "integrations"


def adapter_packages() -> set[str]:
    """Concrete adapter packages: app/integrations/<category>/<adapter>/ directories. The
    category packages themselves (commerce, messaging, ...) hold only the contracts."""
    return {
        f"app.integrations.{category.name}.{adapter.name}"
        for category in INTEGRATIONS.iterdir() if category.is_dir()
        for adapter in category.iterdir()
        if adapter.is_dir() and (adapter / "__init__.py").exists()
    }  # fmt: skip


def imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def test_only_the_composition_root_imports_a_concrete_adapter() -> None:
    adapters = adapter_packages()
    assert "app.integrations.commerce.mock" in adapters  # the guard sees real packages
    for path, source in production_sources():
        relative = path.relative_to(APP.parent).with_suffix("").as_posix().replace("/", ".")
        if relative.startswith("app.composition"):
            continue  # the composition root wires adapters to Product contracts
        for module in imported_modules(source):
            for adapter in adapters:
                if module == adapter or module.startswith(adapter + "."):
                    assert relative.startswith(adapter), (relative, module)


def test_contract_packages_never_load_an_adapter() -> None:
    contracts = sorted({a.rsplit(".", 1)[0] for a in adapter_packages()}
                       | {"app.integrations.messaging"})  # fmt: skip
    code = (
        f"import sys; import {', '.join(contracts)}; "
        f"print(sorted(m for m in sys.modules if m.startswith({tuple(adapter_packages())!r})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=API,
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_the_agent_and_tool_layer_never_touches_integration_secrets() -> None:
    """Provider credentials live behind integration management; the Agent / model / tool
    layer can never import secret storage, so an LLM never receives a credential."""
    for path, source in production_sources():
        relative = path.relative_to(APP).as_posix()
        if not relative.startswith(("agents/", "operations/", "workflows/", "runtime/")):
            continue
        for module in imported_modules(source):
            assert not module.startswith(
                (
                    "app.integration_management.secrets",
                    "app.integration_management.filesystem_secrets",
                    "app.integration_management.drivers",
                )
            ), (path, module)


_REAL_PROVIDERS = ("shopify", "woocommerce", "whatsapp", "twilio", "google ads",
                   "".join(("ful", "fly")))  # fmt: skip
GENERIC_FAKES = (
    SUPPORT, ROOT / "tests" / "support" / "integration_fakes.py",
    ROOT / "tests" / "support" / "conversation_fakes.py",
)  # fmt: skip


def test_the_test_only_generic_fakes_never_impersonate_a_real_provider() -> None:
    """The acceptance harness proves contract composability with GENERIC fakes. Real
    provider adapters are allowed in production behind the boundary above; these TEST-ONLY
    fakes simply must never model one."""
    for path in GENERIC_FAKES:
        code = "\n".join(line for line in path.read_text().lower().splitlines()
                         if not line.lstrip().startswith("#"))  # fmt: skip
        for provider in _REAL_PROVIDERS:
            assert provider not in code, (path.name, provider)
        assert not re.search(r"\bmeta\b", code), path.name


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


# ----- 6. the explicit CI gate and the historical release documents -----------------------------


def test_ci_runs_the_named_product_regression_acceptance_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["backend"]["steps"]
    names = [s.get("name", "") for s in steps]
    gate = names.index("Product regression acceptance")
    full = next(i for i, s in enumerate(steps) if "pytest" in s.get("run", "")
                and "tests/acceptance" not in s.get("run", ""))  # fmt: skip
    migrate = next(i for i, s in enumerate(steps) if "alembic" in s.get("run", ""))
    assert migrate < full < gate
    step = steps[gate]
    assert "uv run pytest tests/acceptance" in step["run"]
    assert step["env"]["REQUIRE_INTEGRATION_TESTS"] == "1"  # PostgreSQL missing: FAIL
    # APP_DATABASE_URL comes from the job environment (.env.example, the CI database).
    assert workflow["jobs"]["backend"]["env"]["REQUIRE_INTEGRATION_TESTS"] == "1"


def test_the_historical_release_documents_state_the_release_boundaries() -> None:
    """The release documents describe the Product Core release at its commit (they are
    pinned byte for byte above); their limitations are historical, not current."""
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
    # The record itself is self-finalizing too: an objective gate, never a static status.
    for static in ("Status: Task 040 acceptance candidate", "Status: candidate",
                   "Status: final"):  # fmt: skip
        assert static not in record, static
    for concept in ("Status rule:", "Task 040 acceptance candidate while the Task 040 commit "
                    "is not yet on `main`", "merged to `main`",
                    "push-to-main CI on that exact merge commit is green",
                    "final Product Core acceptance record", "provider-free",
                    "no follow-up documentation commit is required"):  # fmt: skip
        assert concept in record, concept
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
