"""Command-layer boundaries: product core above execution, persistence-independent."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app
import app.commands

APP_DIR = Path(app.__file__).parent
COMMANDS_DIR = Path(app.commands.__file__).parent
COMMAND_FILES = sorted(COMMANDS_DIR.rglob("*.py"))

ALLOWED_ROOTS = {
    "__future__", "collections", "enum", "hashlib", "hmac", "json", "math", "types",
    "typing", "uuid", "pydantic",
}  # fmt: skip
ALLOWED_APP = ("app.commands", "app.context.models", "app.governance", "app.execution")


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_package_modules() -> None:
    assert {p.name for p in COMMAND_FILES} == {
        "__init__.py", "coordinator.py", "errors.py", "fingerprint.py", "models.py", "store.py",
    }  # fmt: skip


def test_commands_import_only_stdlib_pydantic_context_governance_and_execution() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in COMMAND_FILES
        for m in imports(p)
        if not m.startswith(ALLOWED_APP) and m.split(".")[0] not in ALLOWED_ROOTS
    ]
    assert offenders == []


def test_commands_never_load_infrastructure_at_runtime() -> None:
    # (fastapi is excluded here only because importing ``app.context.models`` runs the
    # pre-existing ``app.context`` package init; the static import check above covers it.)
    code = (
        "import sys, app.commands;"
        "bad = [m for m in ('sqlalchemy', 'psycopg', 'agno', 'redis', 'alembic',"
        " 'app.persistence', 'app.integrations', 'app.operations', 'app.agents',"
        " 'app.routes', 'app.runtime') if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd="apps/api"
    )
    assert out.stdout.strip() == ""


def test_execution_does_not_depend_on_commands_or_persistence() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in sorted((APP_DIR / "execution").rglob("*.py"))
        for m in imports(p)
        if m.startswith(("app.commands", "app.persistence"))
    ]
    assert offenders == []


def test_governance_does_not_depend_on_commands_or_persistence() -> None:
    offenders = [
        f"{p.name}: {m}"
        for p in sorted((APP_DIR / "governance").rglob("*.py"))
        for m in imports(p)
        if m.startswith(("app.commands", "app.persistence"))
    ]
    assert offenders == []


def test_coordinator_wraps_the_execution_coordinator() -> None:
    from app.commands import coordinator
    from app.execution import ExecutionCoordinator

    assert coordinator.ExecutionCoordinator is ExecutionCoordinator


def test_no_in_memory_or_redis_idempotency_and_no_recovery_machinery() -> None:
    found = {
        t.string.lower()
        for p in COMMAND_FILES
        for t in tokenize.generate_tokens(io.StringIO(p.read_text()).readline)
        if t.type == tokenize.NAME
    }
    forbidden = {
        "lru_cache", "cache", "redis", "cachetools", "timeout", "stale", "takeover", "sleep",
        "retry", "worker", "cron", "delete", "asyncio", "threading", "global",
    }  # fmt: skip
    assert found & forbidden == set()
