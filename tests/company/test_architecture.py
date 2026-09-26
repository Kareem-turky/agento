"""The Company Operating Model is pure configuration: no runtime, framework or provider."""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

import app.company

COMPANY_DIR = Path(app.company.__file__).parent
SOURCE_FILES = sorted(COMPANY_DIR.rglob("*.py"))

ALLOWED_IMPORT_ROOTS = {
    "__future__", "collections", "datetime", "decimal", "enum", "typing", "uuid", "pydantic",
}  # fmt: skip
ALLOWED_APP_MODULES = ("app.company", "app.commerce.domain")
FORBIDDEN_NAMES = {
    "shopify", "woocommerce", "woo", "fulfly", "agno", "fastapi", "sqlalchemy",
    "os", "environ", "getenv", "eval", "exec", "compile", "open", "prompt", "secret",
}  # fmt: skip
FORBIDDEN_MODULES = (
    "agno", "fastapi", "starlette", "sqlalchemy", "psycopg", "redis", "openai", "anthropic",
    "yaml", "httpx",
)  # fmt: skip


def test_package_has_expected_modules() -> None:
    assert {p.name for p in SOURCE_FILES} >= {
        "model.py", "sla.py", "escalation.py", "kpi.py", "reporting.py", "capabilities.py",
    }  # fmt: skip


def test_imports_are_limited_to_stdlib_pydantic_and_the_commerce_domain() -> None:
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
                if module.startswith(ALLOWED_APP_MODULES):
                    continue
                if module.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    offenders.append(f"{path.name}: {module}")

    assert offenders == []


def test_code_never_names_providers_runtimes_env_or_executable_hooks() -> None:
    """Scans identifiers only (comments and docstrings may explain the architecture)."""
    offenders = []
    for path in SOURCE_FILES:
        tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
        for token in tokens:
            if token.type == tokenize.NAME:
                words = set(token.string.lower().split("_")) | {token.string.lower()}
                if words & FORBIDDEN_NAMES:
                    offenders.append(f"{path.name}:{token.start[0]} {token.string}")

    assert offenders == []


def test_importing_the_operating_model_loads_no_runtime_framework_or_parser() -> None:
    code = (
        "import sys; import app.company.operating_model; "
        f"print(sorted({{m.split('.')[0] for m in sys.modules}} & set({FORBIDDEN_MODULES!r})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=COMPANY_DIR.parent.parent,
    )

    assert result.stdout.strip() == "[]"
