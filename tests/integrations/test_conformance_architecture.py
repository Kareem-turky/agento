"""The generic conformance harness is provider-independent (no mock knowledge)."""

import ast
import subprocess
import sys
from pathlib import Path

import tests.commerce_conformance

PACKAGE = Path(tests.commerce_conformance.__file__).parent
FILES = sorted(PACKAGE.rglob("*.py"))
ROOT = PACKAGE.parents[1]
ALLOWED_APP = ("app.integrations.commerce", "app.commerce.domain")
ALLOWED_ROOTS = {"__future__", "asyncio", "collections", "dataclasses", "datetime", "inspect",
                 "re", "typing", "uuid", "tests"}  # fmt: skip
PROVIDER_NAMES = ("mock-commerce", "MockCommerce", "canonical_id", "EntityType", "shop_",
                  "ord_", "ship_", "acct_", "sku-", "loc_", "cus_", "tkt_")  # fmt: skip


def imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_generic_package_imports_only_the_contract_domain_and_stdlib() -> None:
    assert {p.name for p in FILES} == {"__init__.py", "contract.py", "suite.py"}
    offenders = [
        f"{p.name}: {m}" for p in FILES for m in imports(p)
        if not m.startswith(ALLOWED_APP) and m.split(".")[0] not in ALLOWED_ROOTS
    ]  # fmt: skip
    assert offenders == []
    for path in FILES:
        bad = [m for m in imports(path) if m.startswith((
            "app.integrations.commerce.mock", "app.agents", "app.workflows", "app.persistence",
            "app.commands", "app.execution", "app.routes", "app.composition", "fastapi",
            "sqlalchemy", "httpx", "requests", "tests.integrations"))]  # fmt: skip
        assert bad == [], path.name
        local = [m for m in imports(path) if m.startswith("tests")]
        assert all(m.startswith("tests.commerce_conformance") for m in local), path.name


def test_generic_package_names_no_provider_ids() -> None:
    for path in FILES:
        text = path.read_text()
        for name in PROVIDER_NAMES:
            assert name.lower() not in text.lower(), (path.name, name)


def test_importing_the_harness_loads_no_adapter() -> None:
    code = (
        "import sys, tests.commerce_conformance; "
        "print(sorted(m for m in sys.modules if m.startswith(('app.integrations.commerce.mock', "
        "'app.agents', 'app.persistence', 'fastapi', 'agno', 'sqlalchemy'))))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=ROOT, env={"PYTHONPATH": f"{ROOT}:{ROOT / 'apps' / 'api'}"},
    )  # fmt: skip
    assert result.stdout.strip() == "[]"


def test_no_provider_adapter_beyond_the_mock_exists() -> None:
    commerce = ROOT / "apps" / "api" / "app" / "integrations" / "commerce"
    packages = sorted(p.name for p in commerce.iterdir() if p.is_dir() and p.name != "__pycache__")
    assert packages == ["mock"]  # no real provider adapter exists (none may be guessed)
