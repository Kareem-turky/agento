"""The Product-only OpenAPI contract and API reference (Task 043).

``docs/openapi/agento-product-api-v1.json`` and the generated block of
``docs/API_REFERENCE.md`` are produced by ``apps/api/scripts/export_product_openapi.py``.
These tests prove the checked-in files are exactly what the generator produces from the
code, that the contract is the effective Product surface (no more, no less), that AgentOS
and the Web BFF are absent, that Product auth and ``Idempotency-Key`` are described
exactly where the code needs them, that no secret or environment value is embedded, and
that generation needs no database, network or provider.
"""

import json
import os
import re
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

import tests.api.test_product_http_surface as surface
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY, UNREACHABLE_DATABASE_URL
from tests.support.product_auth import TEST_PRODUCT_KEY

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "export_product_openapi.py"
ARTIFACT = ROOT / "docs" / "openapi" / "agento-product-api-v1.json"
REFERENCE = ROOT / "docs" / "API_REFERENCE.md"
IDEMPOTENT = {
    ("POST", "/api/v1/operations/tickets"),
    ("POST", "/api/v1/chat/ticket-proposals/confirm"),
}
HEALTH = {("GET", "/health"), ("GET", "/health/live"), ("GET", "/health/ready")}
# Fails the generator subprocess on ANY socket connection (database, network, provider).
NO_NETWORK = (
    "import socket\n"
    "def refuse(*a, **k):\n"
    "    raise AssertionError('network access during contract generation')\n"
    "socket.socket.connect = refuse\n"
    "socket.socket.connect_ex = refuse\n"
    "socket.create_connection = refuse\n"
)


def spec() -> dict[str, Any]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def operations(document: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted((m.upper(), p) for p, item in document["paths"].items() for m in item)


def run_generator(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    code = NO_NETWORK + (
        "import runpy, sys\n"
        f"sys.argv = [{str(SCRIPT)!r}, *{list(args)!r}]\n"
        f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
    )
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env if env is not None else dict(os.environ),
        check=False,
    )


def walk(routes: list[Any]) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(walk(route.original_router.routes))
    return found


@pytest.fixture(scope="module")
def combined() -> Any:
    """The REAL combined runtime app (Product + AgentOS attached), as deployed."""
    from agno.os.settings import AgnoAPISettings

    from app.config import Settings

    settings = Settings(_env_file=None, environment="test", database_url=UNREACHABLE_DATABASE_URL)
    return create_app(settings, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY))


def split(app: Any) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    product, runtime = set(), set()
    for route in walk(app.routes):
        target = product if route.endpoint.__module__.startswith("app.") else runtime
        target.update((m, route.path) for m in route.methods or ())
    return product, runtime


# ----- generated, deterministic, drift-checked ------------------------------------------------


def test_checked_in_artifacts_are_exactly_the_generated_ones_offline() -> None:
    # The generator runs with every socket refused and secret-looking environment set: it
    # needs no database, network or provider, and no environment value changes its output.
    env = {
        **os.environ,
        "APP_DATABASE_URL": "postgresql+psycopg://leak:leak@db.invalid/leak",
        "OS_SECURITY_KEY": "env-os-security-key-must-not-appear-0000000",
        "APP_NAME": "env-app-name-must-not-appear",
        "OPENAI_API_KEY": "sk-env-must-not-appear",
    }
    result = run_generator("--check", env=env)
    assert result.returncode == 0, result.stderr
    text = ARTIFACT.read_text(encoding="utf-8")
    for leaked in ("leak:leak", "env-os-security-key", "env-app-name", "sk-env"):
        assert leaked not in text


def test_check_mode_detects_drift(tmp_path: Path) -> None:
    copy = tmp_path / "repo"
    (copy / "docs" / "openapi").mkdir(parents=True)
    (copy / "apps" / "api" / "scripts").mkdir(parents=True)
    (copy / "apps" / "api" / "app").symlink_to(ROOT / "apps" / "api" / "app")
    script = copy / "apps" / "api" / "scripts" / SCRIPT.name
    script.write_text(SCRIPT.read_text())
    (copy / "docs" / "openapi" / ARTIFACT.name).write_text(
        ARTIFACT.read_text().replace("Agento Product API", "Drifted API", 1)
    )
    (copy / "docs" / "API_REFERENCE.md").write_text(REFERENCE.read_text())
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--check"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1 and "out of date" in result.stderr


def test_every_reference_resolves() -> None:
    document = spec()
    text = json.dumps(document)
    refs = set(re.findall(r'"\$ref": "#/components/(schemas|headers)/([^"]+)"', text))
    assert refs
    for kind, name in refs:
        assert name in document["components"][kind], (kind, name)


def test_output_is_stable_json() -> None:
    text = ARTIFACT.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    document = spec()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == "Agento Product API"
    assert "commerce-ai-platform" not in text
    assert "servers" not in document  # deployment-specific: no fake hostname
    for phrase in ("Product API only", "AgentOS", "deployment-specific", "Bearer"):
        assert phrase in document["info"]["description"], phrase
    ids = [o["operationId"] for item in document["paths"].values() for o in item.values()]
    assert len(ids) == len(set(ids))


# ----- exactly the Product surface ------------------------------------------------------------


def test_every_product_operation_exactly_once_and_nothing_else(combined: Any) -> None:
    ops = operations(spec())
    assert len(ops) == len(set(ops)) == 59
    product, _ = split(combined)
    assert set(ops) == product  # the effective Product surface of the real application
    # ...which is exactly what the existing Product route guards pin.
    guarded = set().union(
        *(v for k, v in vars(surface).items() if k.endswith("_ROUTES") and isinstance(v, set))
    )
    assert guarded <= set(ops)
    rest = {op for op in ops if not op[1].startswith(surface.MANAGEMENT_PREFIXES)}
    assert rest == HEALTH | {
        ("GET", "/api/v1/system/status"),
        ("POST", "/api/v1/operations/runs"),
        ("GET", "/api/v1/operations/reports/daily"),
        ("POST", "/api/v1/operations/tickets"),
        ("GET", "/api/v1/operations/tickets/commands"),
    }
    assert set(ops) == guarded | rest


def test_no_agentos_route_and_no_web_bff_route(combined: Any) -> None:
    paths = set(spec()["paths"])
    _, runtime = split(combined)
    runtime_paths = {p for _, p in runtime}
    assert len(runtime_paths) > 50  # AgentOS really is attached to the combined app
    assert paths.isdisjoint(runtime_paths)
    for agentos in (
        "/",
        "/agents",
        "/sessions",
        "/teams",
        "/info",
        "/config",
        "/memories",
        "/traces",
        "/metrics",
        "/registry",
        "/schedules",
        "/approvals",
        "/workflows",
        "/knowledge/content",
        "/databases/all/migrate",
    ):
        assert agentos in runtime_paths, agentos
        assert agentos not in paths, agentos
    assert all(p == "/health" or p.startswith(("/health/", "/api/v1/")) for p in paths)
    assert not [p for p in paths if p.startswith("/api/product")]
    text = ARTIFACT.read_text()
    assert '"/api/product/' not in text
    assert "agno." not in text  # no Agno module, router or schema leaks into the contract


# ----- transport semantics --------------------------------------------------------------------


def test_product_auth_on_every_api_v1_operation_and_never_on_health() -> None:
    document = spec()
    scheme = document["components"]["securitySchemes"]
    assert list(scheme) == ["ProductApiKey"]
    assert scheme["ProductApiKey"]["type"] == "http"
    assert scheme["ProductApiKey"]["scheme"] == "bearer"
    description = scheme["ProductApiKey"]["description"]
    assert "Authorization: Bearer <Product API key>" in description
    assert "NOT the AgentOS `OS_SECURITY_KEY`" in description
    assert "security" not in document  # no global requirement: health stays public
    for method, path in operations(document):
        operation = document["paths"][path][method.lower()]
        if path.startswith("/api/v1/"):
            assert operation["security"] == [{"ProductApiKey": []}], (method, path)
            assert "401" in operation["responses"], (method, path)
        else:
            assert (method, path) in HEALTH
            assert "security" not in operation and "401" not in operation["responses"]


def test_idempotency_key_exactly_where_the_code_reads_it() -> None:
    document = spec()
    marked = set()
    for method, path in operations(document):
        params = document["paths"][path][method.lower()].get("parameters", [])
        keys = [p for p in params if p["name"].lower() == "idempotency-key"]
        if keys:
            marked.add((method, path))
            (key,) = keys
            assert key["in"] == "header" and key["required"] is True
            assert key["schema"]["maxLength"] == 128 and key["schema"]["minLength"] == 1
    assert marked == IDEMPOTENT
    # The route modules that read the header are exactly the two owners of these operations.
    holders = surface.test_only_the_ticket_route_handles_the_idempotency_key_header
    holders()  # still exactly routes/chat.py and routes/operations_tickets.py


def test_every_response_documents_the_server_request_id() -> None:
    document = spec()
    assert document["components"]["headers"]["X-Request-ID"]["schema"]["format"] == "uuid"
    for item in document["paths"].values():
        for operation in item.values():
            for response in operation["responses"].values():
                assert response["headers"]["X-Request-ID"] == {
                    "$ref": "#/components/headers/X-Request-ID"
                }


def test_runtime_returns_a_request_id_on_product_and_health_responses(combined: Any) -> None:
    with TestClient(combined) as client:
        for path in ("/health", "/health/live", "/api/v1/agents"):
            assert client.get(path).headers.get("x-request-id")


def test_public_health_contract_matches_the_runtime(combined: Any) -> None:
    document = spec()
    live = document["paths"]["/health/live"]["get"]["responses"]
    ready = document["paths"]["/health/ready"]["get"]["responses"]
    schemas = document["components"]["schemas"]
    assert schemas["LivenessStatus"]["properties"]["status"]["enum"] == ["alive"]
    assert schemas["ReadinessStatus"]["properties"]["status"]["enum"] == ["ready", "not_ready"]
    assert set(live) == {"200"} and set(ready) == {"200", "503"}
    with TestClient(combined) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        answer = client.get("/health/ready")  # no database here: not ready, nothing else
        assert answer.status_code == 503 and answer.json() == {"status": "not_ready"}
    # The runtime FastAPI docs still hide the two probes (production behaviour unchanged).
    runtime_paths = set(combined.openapi()["paths"])
    assert "/health/live" not in runtime_paths and "/health/ready" not in runtime_paths


# ----- 422: three documented shapes, never one assumed for all --------------------------------

SAFE, DETAIL, CODED = "SafeValidationError", "ErrorDetail", "CodedErrorDetail"
MARKER = "contract-planted-value-7c1e"
OPERATOR_KEY = "test-contract-operator-key-" + "o" * 24
STORE = "0a0a0a0a-0000-4000-8000-000000000001"


def resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def conforms(document: dict[str, Any], value: Any, schema: dict[str, Any]) -> bool:
    """A minimal JSON-Schema check for the error shapes (no extra dependency)."""
    schema = resolve(document, schema)
    if "oneOf" in schema:
        return sum(conforms(document, value, s) for s in schema["oneOf"]) == 1
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list) and all(
            conforms(document, v, schema.get("items", {})) for v in value
        )
    if kind == "object":
        props = schema.get("properties", {})
        if not isinstance(value, dict) or not set(schema.get("required", [])) <= set(value):
            return False
        if schema.get("additionalProperties") is False and not set(value) <= set(props):
            return False
        return all(conforms(document, v, props[k]) for k, v in value.items() if k in props)
    return True


def documented_422(document: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    response = document["paths"][path][method.lower()]["responses"]["422"]
    return response["content"]["application/json"]["schema"]


def variants(schema: dict[str, Any]) -> set[str]:
    refs = schema.get("oneOf", [schema])
    return {r["$ref"].rsplit("/", 1)[-1] for r in refs}


def test_the_three_422_shapes_are_closed_and_exact() -> None:
    schemas = spec()["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ValidationError" not in schemas
    safe = schemas[SAFE]["properties"]["detail"]["items"]
    assert set(safe["properties"]) == {"type", "loc", "msg"}
    assert safe["additionalProperties"] is False and schemas[SAFE]["additionalProperties"] is False
    assert schemas[DETAIL]["properties"]["detail"]["type"] == "string"
    coded = schemas[CODED]["properties"]["detail"]
    assert set(coded["properties"]) == {"message", "code", "field"}
    assert set(coded["required"]) == {"message", "code"}  # field is optional
    assert coded["additionalProperties"] is False


def test_each_operation_documents_exactly_its_422_shapes() -> None:
    document = spec()
    expected = {
        ("GET", "/api/v1/operations/reports/daily"): {SAFE, DETAIL},
        ("POST", "/api/v1/integrations/connections"): {SAFE, DETAIL, CODED},
        ("PUT", "/api/v1/integrations/connection"): {SAFE, DETAIL, CODED},
        ("PUT", "/api/v1/integrations/connection/credentials"): {SAFE, DETAIL, CODED},
        ("POST", "/api/v1/integrations/connection/test"): {SAFE, DETAIL},
        ("GET", "/api/v1/approvals"): {SAFE, CODED},
        ("POST", "/api/v1/approvals/approval/approve"): {SAFE, CODED},
        ("POST", "/api/v1/approvals/approval/reject"): {SAFE, CODED},
        ("POST", "/api/v1/approvals/approval/cancel"): {SAFE, CODED},
        ("GET", "/api/v1/conversations"): {SAFE, CODED},
        ("GET", "/api/v1/conversations/messages"): {SAFE, CODED},
        ("POST", "/api/v1/knowledge/operating-model/publish"): {SAFE, CODED},
        ("POST", "/api/v1/knowledge/document/create"): {SAFE, CODED},
        ("POST", "/api/v1/knowledge/document/version"): {SAFE, CODED},
        ("POST", "/api/v1/knowledge/document/archive"): {SAFE, CODED},
        ("POST", "/api/v1/knowledge/query"): {SAFE, CODED},
    }
    seen = {}
    for method, path in operations(document):
        responses = document["paths"][path][method.lower()]["responses"]
        if "422" in responses:
            seen[(method, path)] = variants(documented_422(document, method, path))
    multi = {op: v for op, v in seen.items() if v != {SAFE}}
    assert multi == expected  # never every shape everywhere: only where the source raises it
    assert all(v == {SAFE} for op, v in seen.items() if op not in expected)
    for op, shapes in expected.items():
        schema = documented_422(document, *op)
        assert len(schema["oneOf"]) == len(shapes), op


def test_runtime_transport_and_fixed_string_422_match_the_contract(settings) -> None:
    from agno.os.settings import AgnoAPISettings

    from tests.support.product_auth import deployment_settings, principal

    keys = (
        principal(
            OPERATOR_KEY,
            key_id="operator",
            actor_id="operator",
            permissions=frozenset({"orders.read", "shipments.read", "stores.read"}),
            store_ids=frozenset({STORE}),
        ),
    )
    configured = deployment_settings(settings, "test", product_api_keys=keys)
    app = create_app(configured, AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY))
    document = spec()
    headers = {"Authorization": f"Bearer {OPERATOR_KEY}"}
    report = "/api/v1/operations/reports/daily"
    with TestClient(app) as client:
        # Transport validation (SafeValidationRoute): a malformed query value and a body.
        bad = client.get(report, headers=headers, params={"store_id": MARKER})
        assert bad.status_code == 422 and MARKER not in bad.text
        assert conforms(document, bad.json(), {"$ref": f"#/components/schemas/{SAFE}"})
        assert conforms(document, bad.json(), documented_422(document, "GET", report))
        run = client.post(
            "/api/v1/operations/runs",
            headers=headers,
            json={"store_id": STORE, "message": MARKER, "extra": MARKER},
        )
        assert run.status_code == 422 and MARKER not in run.text
        assert conforms(
            document, run.json(), documented_422(document, "POST", "/api/v1/operations/runs")
        )
        # The daily report's own 422: a fixed string detail, not SafeValidationError.
        extra = client.get(report, headers=headers, params={"store_id": STORE, "timezone": MARKER})
        assert extra.status_code == 422
        assert extra.json() == {"detail": "Unsupported query parameters"}
        assert not conforms(document, extra.json(), {"$ref": f"#/components/schemas/{SAFE}"})
        assert conforms(document, extra.json(), documented_422(document, "GET", report))


def test_runtime_coded_422_matches_the_contract(settings, tmp_path: Path) -> None:
    from tests.integration_management.test_http_security import World, create

    document = spec()
    world = World(settings, tmp_path)
    create_schema = documented_422(document, "POST", "/api/v1/integrations/connections")
    # Coded, with the optional field: a driver/config refusal.
    coded = create(world, config={"store_url": "https://h.test", "region": "invalid"})
    assert coded.status_code == 422
    detail = coded.json()["detail"]
    assert isinstance(detail, dict) and {"message", "code"} <= set(detail)
    assert set(detail) <= {"message", "code", "field"}
    assert conforms(document, coded.json(), {"$ref": f"#/components/schemas/{CODED}"})
    assert conforms(document, coded.json(), create_schema)
    # Fixed string on the same operation: an integration that is not installed.
    missing = create(world, integration_id="not-installed")
    assert missing.json() == {"detail": "Integration is not installed"}
    assert conforms(document, missing.json(), create_schema)
    # Coded WITHOUT the optional field, from the real runtime (an unknown config key).
    unknown = create(world, config={"store_url": "https://h.test", "unknown": MARKER})
    assert unknown.status_code == 422 and MARKER not in unknown.text
    assert set(unknown.json()["detail"]) == {"message", "code"}
    assert conforms(document, unknown.json(), create_schema)
    # Coded without `field` (Approvals/Conversations/Knowledge shape) is also the schema.
    assert conforms(
        document,
        {"detail": {"message": "m", "code": "c"}},
        {"$ref": f"#/components/schemas/{CODED}"},
    )


# ----- hand-written metadata cannot drift ------------------------------------------------------


def tampered(edit: str) -> subprocess.CompletedProcess:
    code = NO_NETWORK + (
        "import runpy\n"
        f"g = runpy.run_path({str(SCRIPT)!r}, run_name='lib')\n"
        f"{edit}\n"
        "g['build_openapi']()\n"
    )
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=False
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ("g['ENDPOINT_NOTES'][('GET', '/api/v1/gone')] = (None, 'x')", "ENDPOINT_NOTES"),
        (
            "g['UNDECLARED_RESPONSES'][('GET', '/api/v1/gone')] = {'404': 'x'}",
            "UNDECLARED_RESPONSES has stale",
        ),
        ("g['UNDECLARED_RESPONSES'][('GET', '/api/v1/agents')] = {'299': 'x'}", "invalid status"),
        ("g['UNDECLARED_RESPONSES'][('GET', '/api/v1/agents')] = {'422': 'x'}", "PRODUCT_422"),
        (
            "g['UNDECLARED_RESPONSES'][('GET', '/api/v1/agents')] = {'403': 'x'}",
            "would override the route-declared 403",
        ),
        ("g['PRODUCT_422'][('GET', '/api/v1/gone')] = ('ErrorDetail',)", "PRODUCT_422 has stale"),
        ("g['PRODUCT_422'][('GET', '/api/v1/agents')] = ('Anything',)", "variants must be"),
        (
            "del g['PRODUCT_422'][('GET', '/api/v1/operations/reports/daily')]",
            "unlisted ['app.routes.operations_reports']",
        ),
        (
            "g['PRODUCT_422'][('GET', '/api/v1/agents')] = ('ErrorDetail',)",
            "listed without a 422 ['app.routes.agents']",
        ),
    ],
)
def test_stale_or_invalid_hand_written_metadata_fails_generation(edit: str, message: str) -> None:
    result = tampered(edit)
    assert result.returncode != 0
    assert message in result.stderr + result.stdout, result.stderr[-500:]


def test_untampered_metadata_generates() -> None:
    result = tampered("pass")
    assert result.returncode == 0, result.stderr[-500:]


def test_request_schemas_are_as_strict_as_the_pydantic_models(combined: Any) -> None:
    document = spec()
    product = [r for r in walk(combined.routes) if r.endpoint.__module__.startswith("app.")]
    checked = 0
    for route in product:
        for param in route.dependant.body_params:
            annotation = param.field_info.annotation
            models = [
                a
                for a in (*typing.get_args(annotation), annotation)
                if isinstance(a, type) and issubclass(a, BaseModel)
            ]
            for model in models:
                schema = document["components"]["schemas"][model.__name__]
                if model.model_config.get("extra") == "forbid":
                    assert schema.get("additionalProperties") is False, model.__name__
                    checked += 1
    assert checked >= 15  # every Product request body model is strict


# ----- no secret or environment value -----------------------------------------------------------


def test_artifacts_contain_no_secret_or_environment_value() -> None:
    for path in (ARTIFACT, REFERENCE):
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            TEST_OS_SECURITY_KEY,
            TEST_PRODUCT_KEY,
            "contract-generation-only",
            "postgresql+psycopg",
            "postgresql://",
            "localtestpw",
            "/run/secrets",
            "integration-secrets",
            "sk-",
            "demo-product-key-",
            "APP_DATABASE_URL",
            "BEGIN PRIVATE KEY",
        ):
            assert forbidden not in text, (path.name, forbidden)
    assert "OS_SECURITY_KEY=" not in ARTIFACT.read_text()


# ----- the human reference ----------------------------------------------------------------------


def test_reference_covers_every_operation_and_required_sections() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    for method, path in operations(spec()):
        assert text.count(f"#### `{method} {path}`") == 1, (method, path)
    for section in (
        "## 1. Scope",
        "## 2. Base URL",
        "## 3. Versioning",
        "## 4. Authentication",
        "## 5. Authorization and scope",
        "## 6. Request and response conventions",
        "## 7. Idempotency",
        "## 8. Errors",
        "## 9. Important boundaries",
        "## 10. Examples",
        "## 11. Endpoint reference (generated)",
        "## Appendix A. Schemas (generated)",
    ):
        assert section in text, section
    for area in (
        "Health",
        "System",
        "Operations",
        "Integrations",
        "Agents",
        "Skills",
        "Tasks",
        "Workflows",
        "Knowledge",
        "Approvals",
        "Conversations",
        "Employee Chat",
    ):
        assert f"### {area}\n" in text, area
    for placeholder in ("$AGENTO_BASE_URL", "$AGENTO_API_KEY", "$STORE_ID"):
        assert placeholder in text
    docs = (
        "INTEGRATIONS.md",
        "AGENTS.md",
        "SKILLS_AND_TASKS.md",
        "WORKFLOWS.md",
        "KNOWLEDGE.md",
        "APPROVALS.md",
        "CONVERSATIONS.md",
        "EMPLOYEE_CHAT.md",
    )
    for doc in docs:
        assert f"]({doc})" in text and (ROOT / "docs" / doc).exists(), doc


def test_the_generator_is_documentation_tooling_only() -> None:
    # No runtime module imports the generator; the deployment image does not ship it.
    app_dir = ROOT / "apps" / "api" / "app"
    for module in app_dir.rglob("*.py"):
        assert "export_product_openapi" not in module.read_text(), module
    assert "COPY apps/api/scripts" not in (ROOT / "apps" / "api" / "Dockerfile").read_text()
