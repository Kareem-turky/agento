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

ALLOWED_ROOTS = {"__future__", "collections", "hmac", "typing", "uuid", "pydantic", "sqlalchemy"}
# The command store contracts, the audit event contract PostgresAuditSink persists, and
# the contract vocabularies its CHECK constraints are derived from (pure models/enums).
ALLOWED_APP = ("app.persistence", "app.commands", "app.execution.audit",
               "app.execution.models", "app.governance.policy", "app.context.models")  # fmt: skip


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
