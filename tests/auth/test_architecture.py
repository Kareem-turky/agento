"""Product auth boundaries: identity only, constant-time, no persistence or runtime."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app
import app.auth

APP_DIR = Path(app.__file__).parent
AUTH_FILES = sorted(Path(app.auth.__file__).parent.rglob("*.py"))
RESOLVER = APP_DIR / "auth" / "api_keys.py"
ROOT = APP_DIR.parents[2]

ALLOWED_ROOTS = {"__future__", "collections", "hashlib", "hmac", "re", "typing", "pydantic",
                 "starlette"}  # fmt: skip
ALLOWED_APP = ("app.auth", "app.config", "app.context.models", "app.context.resolver")


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


def test_auth_imports_only_stdlib_pydantic_starlette_config_and_context() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in AUTH_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP) and m.split(".")[0] not in ALLOWED_ROOTS
    ]
    assert offenders == []


def test_auth_loads_no_business_persistence_or_runtime_code() -> None:
    code = "import sys, app.auth; print('\\n'.join(sorted(sys.modules)))"
    out = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=APP_DIR.parent,
    )  # fmt: skip
    loaded = set(out.stdout.split())
    assert not {m.split(".")[0] for m in loaded} & {"agno", "sqlalchemy", "psycopg", "redis",
                                                   "alembic", "jwt", "jose"}  # fmt: skip
    assert not [m for m in loaded if m.startswith((
        "app.commands", "app.persistence", "app.execution", "app.governance", "app.operations",
        "app.integrations", "app.agents", "app.routes", "app.runtime", "app.application",
    ))]  # fmt: skip


def test_resolver_uses_sha256_and_constant_time_comparison_only() -> None:
    tree = ast.parse(RESOLVER.read_text())
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "hmac.compare_digest" in calls and "hashlib.sha256" in calls
    # No ==/!=/in comparison involves the token, the digest or a configured hash.
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            text = ast.unparse(node)
            for secret in ("token", "digest", "key_sha256"):
                assert secret not in text or "len(" in text or "is None" in text, text
    # The raw token is only hashed, never compared or stored.
    resolve = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "resolve")  # fmt: skip
    compare_args = [
        ast.unparse(a)
        for n in ast.walk(resolve)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "hmac.compare_digest"
        for a in n.args
    ]
    assert compare_args == ["digest", "principal.key_sha256"]
    assert not [n for n in ast.walk(resolve) if isinstance(n, ast.Attribute)
                and isinstance(n.ctx, ast.Store)]  # fmt: skip
    assert "break" not in RESOLVER.read_text()  # every configured hash is compared


def test_resolver_decides_no_authorization_and_logs_nothing() -> None:
    used = names(RESOLVER)
    for forbidden in ("PolicyOutcome", "GovernanceGate", "required_permission", "logger",
                      "logging", "print", "state", "setattr", "tenant"):  # fmt: skip
        assert forbidden not in used, forbidden


def test_no_auth_persistence_jwt_sessions_or_key_management() -> None:
    source = "\n".join(p.read_text() for p in sorted(APP_DIR.rglob("*.py"))).lower()
    for word in ("jwt", "oauth", "oidc", "refresh_token", "set_cookie", "set-cookie",
                 "session_cookie", "api_keys_table", "/api-keys", "/auth/"):  # fmt: skip
        assert word not in source, word
    migrations = sorted((ROOT / "apps" / "api" / "migrations" / "versions").glob("*.py"))
    # Product migrations only (no auth tables): exactly these files.
    assert [m.name for m in migrations] == [
        "0001_create_write_commands.py",
        "0002_create_audit_events.py",
        "0003_create_integration_connections.py",
        "0004_create_agent_configurations.py",
        "0005_create_workflow_runtime.py",
        "0006_create_knowledge_context.py",
        "0007_create_approvals.py",
    ]
    # 0003 stores integration connection metadata, 0004 Agent enable/disable overrides and
    # 0005 Workflow execution control state: no user, key, session or token table.
    for migration in migrations[2:]:
        for word in ("api_key", "session", "token", "password", "jwt"):
            assert word not in migration.read_text().lower(), (migration.name, word)


def test_no_raw_secret_in_env_example_or_docs() -> None:
    env = (ROOT / ".env.example").read_text()
    assert "APP_PRODUCT_AUTH_MODE=disabled" in env
    assert "APP_PRODUCT_API_KEYS=[]" in env
    for doc in (ROOT / "README.md", ROOT / "apps" / "api" / "README.md", ROOT / ".env.example"):
        text = doc.read_text()
        assert "Bearer test-" not in text and "test-product-key" not in text
