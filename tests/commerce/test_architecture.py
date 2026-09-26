"""The canonical commerce domain stays provider-, runtime- and framework-independent."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app.commerce

COMMERCE_DIR = Path(app.commerce.__file__).parent
SOURCE_FILES = sorted(COMMERCE_DIR.rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {"__future__", "datetime", "decimal", "enum", "typing", "uuid", "pydantic"}
FORBIDDEN_NAMES = {"shopify", "woocommerce", "woo", "fulfly", "agno", "fastapi", "sqlalchemy"}
FORBIDDEN_MODULES = (
    "agno",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "psycopg",
    "redis",
    "openai",
    "anthropic",
)


def test_domain_has_source_files() -> None:
    assert {p.name for p in SOURCE_FILES} >= {
        "common.py", "company.py", "catalog.py", "customer.py",
        "inventory.py", "orders.py", "shipping.py",
    }  # fmt: skip


def test_imports_are_limited_to_stdlib_pydantic_and_the_domain_itself() -> None:
    offenders = []
    for path in SOURCE_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith("app.commerce"):
                    continue
                if module.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    offenders.append(f"{path.name}: {module}")

    assert offenders == []


def test_code_never_names_providers_or_runtimes() -> None:
    """Scans identifiers only (comments and docstrings may explain the architecture)."""
    offenders = []
    for path in SOURCE_FILES:
        tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
        for token in tokens:
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_"))
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")

    assert offenders == []


def test_importing_the_domain_loads_no_runtime_or_framework() -> None:
    code = (
        "import sys; import app.commerce.domain; "
        f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=COMMERCE_DIR.parents[1],
    )

    assert result.stdout.strip() == "[]"
