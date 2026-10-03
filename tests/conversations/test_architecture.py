"""Architecture guards for Task 037 (Provider-Agnostic Channel & Conversation Foundation)."""

import ast
import hashlib
import re
from pathlib import Path

import app
import app.conversations
import app.integrations.messaging

APP = Path(app.__file__).parent
ROOT = APP.parents[2]
CONVERSATIONS = Path(app.conversations.__file__).parent
MESSAGING = Path(app.integrations.messaging.__file__).parent
VERSIONS = ROOT / "apps/api/migrations/versions"
MIGRATION = VERSIONS / "0008_create_conversations.py"
WEB = ROOT / "apps/web"
TASK_FILES = [*sorted(CONVERSATIONS.glob("*.py")), *sorted(MESSAGING.glob("*.py")),
              APP / "persistence/conversations.py", APP / "routes/conversations.py",
              APP / "composition/conversations.py", MIGRATION]  # fmt: skip
PROVIDERS = ("whatsapp", "meta", "twilio", "telegram", "messenger", "instagram", "sendgrid",
             "mailgun", "vonage", "slack", "shopify", "woocommerce", "f" + "ulfly")  # fmt: skip
# Byte-for-byte as on main before Task 037 (0cece07): conversations change none of them.
PROTECTED = {
    "agents/operations.py": "0e56bb75a6e6decc9285cb8a4774f97696e25ef6359e4cca96d03a5a55a2cfcb",
    "agents/operations_context.py":
        "f2066c40af944302a98512129528183c12929a0fd69efde5e0b6b12787e498d9",
    "agents/operations_tools.py":
        "21012d7629512df0d064848169c49f38132ddd6f38fa0dc5842ab6d7486501f4",
    "agents/generic_reasoning.py":
        "dcf0e78ee250933e1b5fb2017f590ca4701abe7e28b4ec3c57b860d53753d953",
    "workflows/operations_daily_platform.py":
        "736963930573a5cf10d35e03dce5d432c191d4360079a2a9a3544d2dd692e4a0",
    "workflow_management/catalog.py":
        "c429500b534a7835dd8b00cfa216825fa8355253fccea9b7198297686ba2f7ad",
    "workflow_management/definitions.py":
        "cf637677296016e1cefbd7737af2aede68c6191ae08e6492a8ee439de11a778f",
    "workflow_management/engine.py":
        "70bcd58cdf3529239cfc251effaef2722c01aec5eebdc15b0d4d2ea64f03964f",
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
    "integration_management/definitions.py":
        "4d1c2d5c5edda464858f7391fc1882e719f4ebec7f4c8eff7d3206da17fd061c",
    "integration_management/connections.py":
        "64af5160b421b66cfcaf89948120f427f81cfa28add5c5eb5f3e2f809aeff410",
    "integration_management/secrets.py":
        "7a0be75709f388335cfbe36661f79403bb3d7519371726811bf713d156f2835b",
    "composition/registry.py":
        "bb6e276c66d0588c4004ee47d3e707b2c8d938821923b3c755d808a566fc5a78",
    "execution/coordinator.py":
        "33db16943d2692b97bfe07c3bc6b1def100dcb25d7f0b176461ef0b86c8922b4",
    "execution/approvals.py":
        "ee544f009cb12b2109abfb28b1a34d440363d1a7fe14d5557fb8a2a48616aa3a",
    "commands/coordinator.py":
        "14c671da65ba3e5b0af9826f4452f765d6a33af6f09988eb2b845939b7fcb27c",
}  # fmt: skip
# Every Knowledge (Task 035) and Approval (Task 036) source file, as one digest each.
KNOWLEDGE_DIGEST = "ebd8928176e8568e515f2b9c1f0c3b07749e8716f5baac8e92ffaba56972c832"
APPROVALS_DIGEST = "c145a0a7b85aecac7eaa37d7f864b7338254f22d03fc4a210699f1579369c9d5"
MIGRATIONS_0001_TO_0007 = {
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
    "0007_create_approvals.py":
        "0bab564c73a9bcc9a1e24568c1627e707b813f37fcd172d41a48df26b280fde4",
}  # fmt: skip
PYPROJECT_SHA256 = "b5da90a5d5157ee54af92d388d6f76705da27a6241808e980ee1d89b636184eb"
UV_LOCK_SHA256 = "db55fc9c21e8f903ca329875eb9d14c5a11486ffb4aac7f2624544b267e7c638"


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def code(path: Path) -> str:
    """Source without docstrings and comments."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):  # fmt: skip
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined]
    return ast.unparse(tree)


def digest(files: list[Path]) -> str:
    h = hashlib.sha256()
    for path in files:
        h.update(str(path.relative_to(APP)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def test_package_layout() -> None:
    assert {p.name for p in CONVERSATIONS.glob("*.py")} == {
        "__init__.py", "models.py", "delivery.py", "contracts.py", "errors.py",
        "permissions.py", "ingress.py", "service.py",
    }  # fmt: skip
    assert {p.name for p in MESSAGING.glob("*.py")} == {
        "__init__.py",
        "capabilities.py",
        "contract.py",
        "errors.py",
        "registry.py",
    }


def test_the_domain_is_runtime_transport_and_provider_independent() -> None:
    roots = {"collections", "dataclasses", "datetime", "enum", "hashlib", "json", "types",
             "typing", "unicodedata", "uuid", "pydantic"}  # fmt: skip
    allowed = ("app.conversations", "app.context.models", "app.governance",
               "app.integrations.messaging", "app.observability.contracts",
               "app.integration_management")  # fmt: skip
    for path in sorted(CONVERSATIONS.glob("*.py")):
        for module in imports(path):
            assert module.split(".")[0] in roots or module.startswith(allowed), (path.name,
                                                                                module)  # fmt: skip
    for path in sorted(MESSAGING.glob("*.py")):
        for module in imports(path):
            assert module.split(".")[0] in roots or module.startswith(
                "app.integrations.messaging"
            ), (path.name, module)


def test_no_agno_agent_model_network_secret_or_other_subsystem() -> None:
    forbidden = ("agno", "openai", "anthropic", "httpx", "requests", "socket", "urllib",
                 "aiohttp", "sqlalchemy", "fastapi", "starlette", "app.agents", "app.runtime",
                 "app.knowledge", "app.approval_management", "app.workflow_management",
                 "app.workflows", "app.execution", "app.commands", "app.services",
                 "app.integration_management.secrets",
                 "app.integration_management.filesystem_secrets",
                 "app.integration_management.service", "app.integration_management.handlers",
                 "logging")  # fmt: skip
    for path in [*CONVERSATIONS.glob("*.py"), *MESSAGING.glob("*.py")]:
        for module in imports(path):
            assert not module.startswith(forbidden), (path.name, module)
        text = code(path)
        for word in ("SecretStr", "get_secret_value", "secret_store", "logger"):
            assert word not in text, (path.name, word)
        assert not re.search(r"\bprint\(", text), path.name


def test_no_dynamic_code_worker_or_provider_anywhere_in_task_037() -> None:
    for path in TASK_FILES:
        text = code(path)
        for call in ("importlib", "pkgutil", "entry_points", "__import__(", "exec(", "eval(",
                     "pickle", "subprocess", "os.system", "urlopen", "http://", "https://",
                     "create_task", "ensure_future", "threading", "asyncio.sleep",
                     "time.sleep", "while True", "scheduler", "celery", "kafka",
                     "websocket"):  # fmt: skip
            assert call not in text, (path.name, call)
        lower = path.read_text().lower()
        for provider in PROVIDERS:
            assert not re.search(rf"\b{provider}\b", lower), (path.name, provider)
        for cloud in ("agno cloud", "control plane", "os.agno.com"):
            assert cloud not in lower, (path.name, cloud)


def test_no_public_ingest_webhook_or_send_route() -> None:
    routes = code(APP / "routes/conversations.py")
    assert len(re.findall(r"@router\.get\(", routes)) == 3
    assert not re.findall(r"@router\.(post|put|patch|delete)\(", routes)
    paths = re.findall(r'"(/api/v1/conversations[^"]*)"', routes)
    for word in ("inbound", "webhook", "ingest", "send", "reply", "close", "assign"):
        assert not [p for p in paths if word in p], word
    for path in (APP / "routes").glob("*.py"):
        text = code(path)
        assert "ConversationIngress" not in text and "ConversationDelivery" not in text
        route_paths = re.findall(r'"(/api/[^"]*)"', text)
        assert not [p for p in route_paths if "webhook" in p or "inbound" in p], path.name


def test_ingress_and_delivery_are_only_composed_never_called_by_production_code() -> None:
    users = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
             if CONVERSATIONS not in p.parents
             and re.search(r"ConversationIngress|ConversationDelivery", code(p))}  # fmt: skip
    assert users == {"composition/conversations.py"}
    readers = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
               if CONVERSATIONS not in p.parents
               and any(m.startswith("app.conversations") for m in imports(p))}  # fmt: skip
    assert readers == {"main.py", "routes/conversations.py", "composition/conversations.py",
                       "persistence/conversations.py"}  # fmt: skip
    messaging = {
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if MESSAGING not in p.parents
        and any(m.startswith("app.integrations.messaging") for m in imports(p))
    }
    assert messaging == {"conversations/ingress.py", "composition/conversations.py"}


def test_no_agent_tool_skill_or_workflow_uses_conversations() -> None:
    for layer in ("agents", "runtime", "agent_management", "workflows", "workflow_management",
                  "knowledge", "approval_management", "execution", "commands", "governance",
                  "operations", "services", "application"):  # fmt: skip
        for path in (APP / layer).rglob("*.py"):
            text = path.read_text()
            for word in ("app.conversations", "conversations.read", "ConversationReadService",
                         "app.integrations.messaging"):  # fmt: skip
                assert word not in text, (layer, path.name, word)


def test_persistence_has_no_payload_json_or_provider_columns() -> None:
    for path in (MIGRATION, APP / "persistence/conversations.py"):
        text = path.read_text()
        assert "JSONB" not in text and "sa.JSON" not in text, path.name
        columns = re.findall(r"sa\.Column\(\s*\"([a-z_]+)\"", text)
        assert {"text", "content_fingerprint", "external_message_ref"} <= set(columns)
        for column in columns:
            for word in ("payload", "raw", "header", "token", "secret", "credential",
                         "metadata", "json", "phone", "email", "customer", "media",
                         "attachment", "provider"):  # fmt: skip
                assert word not in column, (path.name, column)
    migration = code(MIGRATION)
    assert "integration_connections" not in migration  # no FK to connection metadata
    downgrade = migration.split("def downgrade")[1]
    for word in ("CASCADE", "DROP SCHEMA", "DELETE FROM", "TRUNCATE"):
        assert word not in downgrade, word
    assert set(re.findall(r"drop_table\('(\w+)'", downgrade.replace('"', "'"))) <= set()
    tables = set(re.findall(r"drop_table\((\w+)", downgrade))
    assert tables == {"DELIVERY", "MESSAGES", "CONVERSATIONS"}


def test_0008_is_the_single_head_and_0001_to_0007_are_unchanged() -> None:
    names = sorted(p.name for p in VERSIONS.glob("*.py"))
    assert names == [*sorted(MIGRATIONS_0001_TO_0007), "0008_create_conversations.py"]
    text = MIGRATION.read_text()
    assert 'revision: str = "0008"' in text and 'down_revision: str | None = "0007"' in text
    for name, expected in MIGRATIONS_0001_TO_0007.items():
        assert hashlib.sha256((VERSIONS / name).read_bytes()).hexdigest() == expected, name


def test_protected_files_catalogs_knowledge_and_approvals_are_unchanged() -> None:
    for relative, expected in PROTECTED.items():
        assert hashlib.sha256((APP / relative).read_bytes()).hexdigest() == expected, relative
    knowledge = [*sorted((APP / "knowledge").glob("*.py")), APP / "persistence/knowledge.py",
                 APP / "routes/knowledge.py", APP / "composition/knowledge.py"]  # fmt: skip
    approvals = [*sorted((APP / "approval_management").glob("*.py")),
                 APP / "persistence/approvals.py", APP / "routes/approvals.py",
                 APP / "composition/approvals.py"]  # fmt: skip
    assert digest(knowledge) == KNOWLEDGE_DIGEST and digest(approvals) == APPROVALS_DIGEST

    from app.agent_management.catalog import build_default_agent_catalog
    from app.agent_management.skills import build_default_skill_catalog
    from app.agent_management.tasks import build_default_task_catalog
    from app.composition.registry import build_default_backend_registry
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.operations import OPERATIONS_ACTIONS
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})
    assert {s.skill_id for s in build_default_skill_catalog().definitions()} == {
        "operations.order_inspection", "operations.daily_analysis",
        "operations.ticket_escalation"}  # fmt: skip
    assert {t.task_id for t in build_default_task_catalog().definitions()} == {
        "operations.inspect_order",
        "operations.analyze_daily",
        "operations.escalate_issue",
    }
    assert build_default_workflow_catalog().workflow_ids == frozenset({"operations.daily_report"})
    catalog = build_default_integration_catalog()
    assert len(catalog) == 0 and len(build_default_messaging_registry(catalog)) == 0
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})
    (ticket,) = [a for a in OPERATIONS_ACTIONS if a.name == "operations.ticket.create"]
    assert ticket.risk.value == "low_risk_write"


def test_no_new_dependency() -> None:
    assert hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest() == PYPROJECT_SHA256
    assert hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest() == UV_LOCK_SHA256
    assert "agno[os,postgres,openai,anthropic]==3.0.11" in (ROOT / "pyproject.toml").read_text()
