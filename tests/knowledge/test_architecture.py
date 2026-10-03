"""Architecture guards for Task 035 (Product Knowledge & company operating context)."""

import ast
import hashlib
import re
import tomllib
from pathlib import Path

import app
import app.knowledge

APP = Path(app.__file__).parent
ROOT = APP.parents[2]
PACKAGE = Path(app.knowledge.__file__).parent
MIGRATION = ROOT / "apps/api/migrations/versions/0006_create_knowledge_context.py"
KNOWLEDGE_FILES = [*sorted(PACKAGE.glob("*.py")), APP / "persistence" / "knowledge.py",
                   APP / "routes" / "knowledge.py", APP / "composition" / "knowledge.py",
                   MIGRATION]  # fmt: skip
PROVIDERS = ("shopify", "woocommerce", "whatsapp", "bosta", "shipblu", "meta ads",
             "google ads", "salla", "f" + "ulfly")  # fmt: skip
# Byte-for-byte as merged before Task 035 (main 5dfcafd): Knowledge changes none of them.
# Task 036 (human approvals) legitimately changed execution/coordinator.py and
# workflow_management/engine.py, so their pins were removed (guarded by Task 036 tests).
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
    "company/operating_model/model.py":
        "ea7c664799dc4437d32fe7b52195027f670c4bcd352115628590b1a4e5eed1ed",
    "company/operating_model/capabilities.py":
        "763bcdc8113d54239277a4413211d1d62e1239c05b2b31ac035b95356cbc3834",
    "governance/gate.py": "78e779bbd0eaf3a853a6439b95d3d22cbad940b802b1b8db718660ea3bb6ad24",
    "governance/permissions.py":
        "e539cfd63f02b731706cd2ed87cf012d658747cba70d4d82f2f1bd67d94c6d78",
    "governance/policy.py": "f2326816bcd83e7e5d765fedc39e930357b397d130a7647f678ad51a73433c41",
}  # fmt: skip


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
        "__init__.py", "limits.py", "errors.py", "documents.py", "chunking.py",
        "operating_context.py", "context.py", "contracts.py", "actions.py", "permissions.py",
        "handlers.py", "service.py", "reader.py",
    }  # fmt: skip


def test_the_package_is_runtime_provider_and_transport_independent() -> None:
    allowed_roots = {"collections", "dataclasses", "datetime", "enum", "hashlib", "json", "re",
                     "typing", "unicodedata", "uuid", "pydantic"}  # fmt: skip
    allowed_app = ("app.knowledge", "app.company.operating_model", "app.context.models",
                   "app.governance", "app.execution", "app.observability.contracts")  # fmt: skip
    for path in sorted(PACKAGE.glob("*.py")):
        for module in imports(path):
            assert module.split(".")[0] in allowed_roots or module.startswith(allowed_app), (
                path.name,
                module,
            )


def test_no_agent_runtime_model_provider_or_network_anywhere_in_knowledge() -> None:
    forbidden = ("agno", "openai", "anthropic", "app.agents", "app.runtime", "app.workflows",
                 "app.workflow_management", "app.integrations", "app.integration_management",
                 "app.services", "httpx", "requests", "socket", "urllib", "aiohttp",
                 "langchain", "llama_index", "llama", "sentence_transformers", "tiktoken",
                 "transformers", "pgvector", "numpy", "pypdf", "docx", "bs4", "lxml",
                 "markdown", "mistune")  # fmt: skip
    for path in KNOWLEDGE_FILES:
        for module in imports(path):
            assert not module.startswith(forbidden), (path.name, module)


def test_no_dynamic_code_execution_or_url_loading() -> None:
    for path in KNOWLEDGE_FILES:
        text = path.read_text()
        for call in ("importlib", "pkgutil", "runpy", "__import__(", "entry_points", "exec(",
                     "eval(", "import_module", "globals()[", "pickle", "marshal",
                     "subprocess", "os.system", "urlopen", "http://", "https://",
                     "open("):  # fmt: skip
            assert call not in text, (path.name, call)
        called = {n.func.id for n in ast.walk(ast.parse(text))
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}  # fmt: skip
        assert not called & {"compile", "exec", "eval", "open", "__import__"}, path.name
        lower = text.lower()
        for provider in PROVIDERS:
            assert provider not in lower, (path.name, provider)
        for cloud in ("agno cloud", "control plane", "os.agno.com", "agno_api_key"):
            assert cloud not in lower, (path.name, cloud)


def test_no_embedding_provider_or_vector_only_design() -> None:
    for path in KNOWLEDGE_FILES:
        lower = path.read_text().lower()
        for word in ("embedding(", "embed(", "cosine", "openai", "ivfflat", "hnsw", "pgvector"):
            assert word not in lower, (path.name, word)
        assert not re.search(r"(?<!ts)vector\(", lower), path.name  # lexical tsvector only


def test_governance_execution_and_agents_never_depend_on_knowledge() -> None:
    for layer in ("governance", "execution", "agents", "runtime", "workflows",
                  "workflow_management", "agent_management", "integrations",
                  "integration_management", "commerce", "company", "operations", "services",
                  "application", "auth", "context", "commands", "observability"):  # fmt: skip
        for path in (APP / layer).rglob("*.py"):
            for module in imports(path):
                assert not module.startswith("app.knowledge"), (layer, path.name)


def test_knowledge_is_wired_only_through_composition_main_routes_and_persistence() -> None:
    users = {str(p.relative_to(APP)) for p in APP.rglob("*.py")
             if PACKAGE not in p.parents
             and any(m.startswith("app.knowledge") for m in imports(p))}  # fmt: skip
    assert users == {"main.py", "routes/knowledge.py", "composition/knowledge.py",
                     "persistence/knowledge.py"}  # fmt: skip


def test_composition_never_builds_observability() -> None:
    text = (APP / "composition" / "knowledge.py").read_text()
    assert "build_default_observability" not in text
    assert [m for m in imports(APP / "composition" / "knowledge.py")
            if m.startswith("app.observability")] == ["app.observability.contracts"]  # fmt: skip


def test_no_agent_consumes_knowledge_and_no_knowledge_tool_or_memory_exists() -> None:
    for path in (APP / "agents").rglob("*.py"):
        text = path.read_text()
        for word in ("app.knowledge", "KnowledgeContextReader", "retrieve_context",
                     "CompanyContextBundle", "KnowledgeService"):  # fmt: skip
            assert word not in text, (path.name, word)
    for path in (APP / "runtime").rglob("*.py"):
        text = path.read_text()
        assert "app.knowledge" not in text and "KnowledgeContextReader" not in text, path.name
    tools = (APP / "agents" / "operations_tools.py").read_text().lower()
    assert "knowledge" not in tools and "retrieve_context" not in tools
    for path in KNOWLEDGE_FILES:
        text = path.read_text()
        for word in ("enable_agentic_memory", "MemoryManager", "add_memory", "Toolkit",
                     "@tool", "instructions="):  # fmt: skip
            assert word not in text, (path.name, word)


def test_protected_files_are_unchanged() -> None:
    for relative, digest in PROTECTED.items():
        assert hashlib.sha256((APP / relative).read_bytes()).hexdigest() == digest, relative


def test_capability_enum_is_unchanged_and_only_operations_is_installed() -> None:
    from app.agent_management.catalog import build_default_agent_catalog
    from app.company.operating_model import AgentCapability

    assert [c.value for c in AgentCapability] == [
        "operations",
        "finance",
        "marketing",
        "customer_experience",
        "analytics",
        "growth",
    ]
    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})


def test_integrations_and_backends_are_unchanged() -> None:
    from app.composition.registry import build_default_backend_registry
    from app.integration_management.catalog import build_default_integration_catalog

    assert len(build_default_integration_catalog()) == 0
    assert build_default_backend_registry().backend_ids == frozenset({"mock"})


# The dependency manifests exactly as on main before Task 035 (no new dependency: no
# LangChain, LlamaIndex, embedding client, tokenizer, document parser or vector database).
PYPROJECT_SHA256 = "b5da90a5d5157ee54af92d388d6f76705da27a6241808e980ee1d89b636184eb"
UV_LOCK_SHA256 = "db55fc9c21e8f903ca329875eb9d14c5a11486ffb4aac7f2624544b267e7c638"


def test_no_new_dependencies() -> None:
    assert hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest() == PYPROJECT_SHA256
    assert hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest() == UV_LOCK_SHA256
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    dependencies = " ".join(project.get("dependencies", [])).lower()
    for word in ("langchain", "llama", "embedding", "sentence", "tiktoken", "pypdf", "docx",
                 "unstructured", "chroma", "qdrant", "pinecone", "weaviate", "faiss"):  # fmt: skip
        assert word not in dependencies, word
    assert "agno[os,postgres,openai,anthropic]==3.0.11" in project["dependencies"]


def test_migrations_0001_to_0005_are_unchanged_and_0006_is_the_knowledge_migration() -> None:
    versions = ROOT / "apps/api/migrations/versions"
    names = sorted(p.name for p in versions.glob("*.py"))
    # Task 036 adds 0007 (approvals) and Task 037 0008 (conversations) on top; 0006
    # stays the Knowledge migration.
    assert names[5] == "0006_create_knowledge_context.py" and len(names) == 8
    text = (versions / names[5]).read_text()
    assert 'revision: str = "0006"' in text and 'down_revision: str | None = "0005"' in text
    for word in ("CASCADE", "DROP SCHEMA", "agno_runtime", "write_commands", "audit_events",
                 "workflow_runs", "agent_configurations", "integration_connections"):  # fmt: skip
        assert word not in text.split("def upgrade")[1].split("def downgrade")[0], word


def test_no_secret_or_credential_storage_in_knowledge() -> None:
    for path in KNOWLEDGE_FILES:
        lower = path.read_text().lower()
        for word in ("secretstr", "get_secret_value", "password", "api_key", "credential_",
                     "token="):  # fmt: skip
            assert word not in lower, (path.name, word)


def test_untrusted_text_is_only_ever_a_bound_parameter() -> None:
    text = (APP / "persistence" / "knowledge.py").read_text()
    assert "sa.text(" not in text and "text(" not in text.replace("_text(", "")
    assert 'sa.bindparam("terms", expression)' in text
    assert "plainto_tsquery" not in text and "websearch_to_tsquery" not in text


def test_lexical_retrieval_is_language_neutral_simple_everywhere() -> None:
    """Retrieval v1 is PostgreSQL ``simple`` FTS: no language-specific configuration
    (no stemming, stop words or language detection) in the stored vector or the query."""
    from app.persistence.knowledge import SEARCH_CONFIGURATION, knowledge_chunks

    assert SEARCH_CONFIGURATION == "simple"
    computed = knowledge_chunks.c.search_vector.computed
    assert computed is not None and "to_tsvector('simple'::regconfig, content)" in str(
        computed.sqltext
    )
    assert "to_tsvector('simple'::regconfig, content)" in MIGRATION.read_text()
    for path in KNOWLEDGE_FILES:
        lower = path.read_text().lower()
        for language in ("'english'", '"english"', "'arabic'", '"arabic"', "langdetect",
                         "snowball", "stemmer", "hunspell", "ispell"):  # fmt: skip
            assert language not in lower, (path.name, language)
