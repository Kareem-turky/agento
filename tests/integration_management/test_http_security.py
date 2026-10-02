"""Integration-management HTTP API: Product authentication, permissions, audit, the secret
boundary, safe errors, OpenAPI. Real routes, service, gate, coordinator, handlers and
filesystem secret store; in-memory metadata repository and audit sink (PostgreSQL is
proven in tests/integration/test_integration_management_postgres.py). Deterministic fake
definitions/drivers only: no network, no provider, no model.
"""

import json
import logging
import socket
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.composition.integrations import build_integration_management
from app.execution import ActionHandlerRegistry, AuditEventType, ExecutionCoordinator
from app.governance import ActionCatalog, GovernanceGate
from app.integration_management.actions import INTEGRATION_ACTIONS
from app.integration_management.filesystem_secrets import FilesystemIntegrationSecretStore
from app.integration_management.handlers import build_integration_handlers
from app.integration_management.service import IntegrationManagementService
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.integration_fakes import (
    CRASH_MARKER,
    VALID_KEY,
    InMemoryConnectionRepository,
    RecordingAuditSink,
    StepClock,
    fake_catalog,
)
from tests.support.product_auth import deployment_settings, principal

READER_KEY = "test-integrations-reader-key-" + "r" * 24
MANAGER_KEY = "test-integrations-manager-key-" + "m" * 24
OPERATOR_KEY = "test-integrations-operator-key-" + "o" * 24
SECRET = "test-only-credential-value-" + "s" * 20  # noqa: S105 - test fixture
NEW_SECRET = VALID_KEY

CATALOG = "/api/v1/integrations/catalog"
CONNECTIONS = "/api/v1/integrations/connections"
CONNECTION = "/api/v1/integrations/connection"
CREDENTIALS = "/api/v1/integrations/connection/credentials"
TEST = "/api/v1/integrations/connection/test"
ENABLE = "/api/v1/integrations/connection/enable"
DISABLE = "/api/v1/integrations/connection/disable"


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


class World:
    def __init__(self, settings, tmp_path: Path, *, with_secret_store: bool = True) -> None:
        self.catalog, self.drivers = fake_catalog()
        self.repository = InMemoryConnectionRepository()
        self.audit = RecordingAuditSink()
        self.root = tmp_path / "integration-secrets"
        self.root.mkdir(mode=0o700)
        self.secrets = FilesystemIntegrationSecretStore(self.root) if with_secret_store else None
        gate = GovernanceGate(ActionCatalog(INTEGRATION_ACTIONS))
        handlers = ActionHandlerRegistry(build_integration_handlers(
            self.catalog, self.repository, self.secrets, clock=StepClock(),
            test_timeout_seconds=0.2))  # fmt: skip
        coordinator = ExecutionCoordinator(gate, handlers, self.audit)
        self.service = IntegrationManagementService(self.catalog, self.repository, self.secrets,
                                                    gate, coordinator)  # fmt: skip
        configured = deployment_settings(settings, "test", product_api_keys=(
            principal(READER_KEY, key_id="reader", actor_id="reader",
                      permissions=frozenset({"integrations.read"})),
            principal(MANAGER_KEY, key_id="manager", actor_id="manager",
                      permissions=frozenset({"integrations.read", "integrations.manage"})),
            principal(OPERATOR_KEY, key_id="operator", actor_id="operator",
                      permissions=frozenset({"orders.read", "tickets.create"})),
        ))  # fmt: skip
        runtime = AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY, docs_enabled=True)
        self.client = TestClient(create_app(configured, runtime, integration_service=self.service))

    def everything(self) -> str:
        """All Product-side state that is NOT the secret store: metadata and audit."""
        rows = [c.model_dump(mode="json") for c in self.repository.rows.values()]
        events = [e.model_dump(mode="json") for e in self.audit.events]
        return json.dumps(rows) + json.dumps(events)


@pytest.fixture
def world(settings, tmp_path: Path) -> World:
    return World(settings, tmp_path)


def create(world: World, key: str = MANAGER_KEY, **overrides) -> object:
    body = {"integration_id": "example-commerce", "display_name": "Main store",
            "config": {"store_url": "https://shop.example.test", "sandbox": True},
            "credentials": {"api_key": SECRET}} | overrides  # fmt: skip
    return world.client.post(CONNECTIONS, json=body, headers=auth(key))


# ----- authentication and permissions ---------------------------------------------------------

ALL_ROUTES = [("GET", CATALOG), ("GET", CONNECTIONS), ("POST", CONNECTIONS), ("GET", CONNECTION),
              ("PUT", CONNECTION), ("DELETE", CONNECTION), ("PUT", CREDENTIALS), ("POST", TEST),
              ("POST", ENABLE), ("POST", DISABLE)]  # fmt: skip
MUTATIONS = [r for r in ALL_ROUTES if r[0] != "GET"]


def test_every_route_requires_product_authentication(world: World) -> None:
    params = {"connection_id": str(uuid4())}
    for method, path in ALL_ROUTES:
        for headers in ({}, auth("test-wrong-key-" + "w" * 32), auth(TEST_OS_SECURITY_KEY)):
            response = world.client.request(method, path, params=params, headers=headers,
                                            json={})  # fmt: skip
            assert response.status_code == 401, (method, path)
    assert world.audit.events == []


def test_reads_need_integrations_read(world: World) -> None:
    for path in (CATALOG, CONNECTIONS):
        assert world.client.get(path, headers=auth(OPERATOR_KEY)).status_code == 403
        assert world.client.get(path, headers=auth(READER_KEY)).status_code == 200
    assert world.client.get(CONNECTION, params={"connection_id": str(uuid4())},
                            headers=auth(OPERATOR_KEY)).status_code == 403  # fmt: skip


def test_mutations_need_integrations_manage_and_denials_are_audited(world: World) -> None:
    created = create(world).json()["connection"]
    params = {"connection_id": created["connection_id"]}
    bodies = {CONNECTIONS: {"integration_id": "example-messaging", "display_name": "x"},
              CONNECTION: {"display_name": "renamed"},
              CREDENTIALS: {"credentials": {"api_key": "k"}}}  # fmt: skip
    before = len(world.audit.events)
    for key in (READER_KEY, OPERATOR_KEY):
        for method, path in MUTATIONS:
            response = world.client.request(method, path, params=params, headers=auth(key),
                                            json=bodies.get(path, {}))  # fmt: skip
            assert response.status_code == 403, (key[:20], method, path)
    denied = [e for e in world.audit.events[before:] if e.event_type is AuditEventType.DENIED]
    assert len(denied) == 2 * len(MUTATIONS)
    assert {e.policy_reason.value for e in denied} == {"permission_denied"}
    # Nothing changed.
    connection = world.client.get(CONNECTION, params=params, headers=auth(READER_KEY)).json()
    assert connection["connection"]["display_name"] == "Main store"


# ----- the full manager lifecycle --------------------------------------------------------------


def test_manager_lifecycle_is_governed_audited_and_never_exposes_secrets(
    world: World, caplog: pytest.LogCaptureFixture, no_network: list[object]
) -> None:
    caplog.set_level(logging.DEBUG)
    response = create(world)
    assert response.status_code == 201
    connection = response.json()["connection"]
    cid = connection["connection_id"]
    assert connection["configured_secret_fields"] == ["api_key"]
    assert connection["config"] == {"store_url": "https://shop.example.test", "sandbox": True}
    assert (connection["enabled"], connection["last_test_result"]) == (True, "never_tested")
    assert connection["last_tested_at"] is None
    assert (world.root / f"{cid}.json").exists()
    params = {"connection_id": cid}

    # Wrong key: the test runs, its FAILURE is the recorded last-known state.
    tested = world.client.post(TEST, params=params, headers=auth(MANAGER_KEY))
    assert tested.status_code == 200
    state = tested.json()["connection"]
    assert (state["last_test_result"], state["last_test_error"]) == (
        "failure",
        "authentication_failed",
    )
    assert state["last_tested_at"] is not None
    driver = world.drivers["example-commerce"]
    assert driver.tests[-1][1] == {"api_key": SECRET}  # the driver got the stored secret

    # Updating NON-secret config keeps the credentials and resets the stale test state.
    updated = world.client.put(CONNECTION, params=params, headers=auth(MANAGER_KEY),
                               json={"config": {"store_url": "https://shop.example.test",
                                                "region": "eu"}})  # fmt: skip
    assert updated.status_code == 200
    state = updated.json()["connection"]
    assert state["configured_secret_fields"] == ["api_key"]
    assert state["config"] == {"store_url": "https://shop.example.test", "region": "eu"}
    assert state["last_test_result"] == "never_tested"
    renamed = world.client.put(CONNECTION, params=params, headers=auth(MANAGER_KEY),
                               json={"display_name": "Renamed"})  # fmt: skip
    assert renamed.json()["connection"]["display_name"] == "Renamed"

    # Explicit credential replacement, then a successful test.
    replaced = world.client.put(CREDENTIALS, params=params, headers=auth(MANAGER_KEY),
                                json={"credentials": {"api_key": NEW_SECRET}})  # fmt: skip
    assert replaced.status_code == 200
    ok = world.client.post(TEST, params=params, headers=auth(MANAGER_KEY)).json()["connection"]
    assert (ok["last_test_result"], ok["last_test_error"]) == ("success", None)

    disabled = world.client.post(DISABLE, params=params, headers=auth(MANAGER_KEY))
    assert disabled.json()["connection"]["enabled"] is False
    enabled = world.client.post(ENABLE, params=params, headers=auth(MANAGER_KEY))
    assert enabled.json()["connection"]["enabled"] is True
    listed = world.client.get(CONNECTIONS, headers=auth(READER_KEY)).json()["connections"]
    assert [c["connection_id"] for c in listed] == [cid]

    deleted = world.client.delete(CONNECTION, params=params, headers=auth(MANAGER_KEY))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert not (world.root / f"{cid}.json").exists()  # secret material removed
    assert world.client.get(CONNECTION, params=params,
                            headers=auth(READER_KEY)).status_code == 404  # fmt: skip

    # Audit: every mutation went through the coordinator and was verified.
    verified = [(e.action_name, e.verification_code) for e in world.audit.events
                if e.event_type is AuditEventType.VERIFIED]  # fmt: skip
    assert verified == [
        ("integrations.connection.create", "connection_created"),
        ("integrations.connection.test", "connection_test_failed"),
        ("integrations.connection.update", "connection_updated"),
        ("integrations.connection.update", "connection_updated"),
        ("integrations.connection.credentials.replace", "credentials_replaced"),
        ("integrations.connection.test", "connection_test_succeeded"),
        ("integrations.connection.disable", "connection_disabled"),
        ("integrations.connection.enable", "connection_enabled"),
        ("integrations.connection.delete", "connection_deleted"),
    ]
    assert {e.actor_id for e in world.audit.events} == {"manager"}
    # The secret values never reached metadata, audit, responses or logs.
    responses = "".join(r.text for r in (response, tested, updated, renamed, replaced,
                                         disabled, enabled, deleted))  # fmt: skip
    for value in (SECRET, NEW_SECRET):
        assert value not in world.everything()
        assert value not in responses
        assert value not in caplog.text
    assert no_network == []


def test_replacement_failure_keeps_the_previous_credentials(world: World, monkeypatch) -> None:
    cid = create(world).json()["connection"]["connection_id"]
    store = world.secrets
    assert store is not None

    async def broken(connection_id: UUID, values: object) -> None:
        from app.integration_management import SecretStoreError

        raise SecretStoreError()

    monkeypatch.setattr(store, "replace", broken)
    response = world.client.put(CREDENTIALS, params={"connection_id": cid},
                                headers=auth(MANAGER_KEY),
                                json={"credentials": {"api_key": NEW_SECRET}})  # fmt: skip
    assert response.status_code == 409
    monkeypatch.undo()
    import asyncio

    kept = asyncio.run(store.read(UUID(cid), frozenset({"api_key"})))
    assert kept["api_key"].get_secret_value() == SECRET


# ----- safe errors -----------------------------------------------------------------------------


def test_validation_errors_never_echo_submitted_values(world: World) -> None:
    planted = "PLANTED-SUBMITTED-VALUE-" + "p" * 12
    cases = [
        {"credentials": {"api_key": planted * 200}},  # too long for the field
        {"credentials": {"api_key": 12345}},  # wrong type
        {"credentials": {"Not A Field": planted}},
        {"config": {"store_url": f"http://{planted}"}},
        {"config": {"store_url": "https://h.test", "unknown": planted}},
        {"config": {"store_url": "https://h.test", "region": "invalid"}},  # driver refusal
        {"display_name": planted * 20},
        {"integration_id": "../" + planted},
    ]
    for overrides in cases:
        response = create(world, **overrides)
        assert response.status_code == 422, overrides
        assert planted not in response.text and "12345" not in response.text
    assert world.repository.rows == {} and list(world.root.iterdir()) == []


def test_unknown_definition_connection_and_malformed_ids_fail_safely(world: World) -> None:
    unknown = create(world, integration_id="not-installed")
    assert (unknown.status_code, unknown.json()["detail"]) == (422, "Integration is not installed")
    delegated = create(world, integration_id="example-marketing", config={}, credentials={})
    assert delegated.status_code == 422
    assert delegated.json()["detail"] == "This build cannot connect this integration"
    for params in ({"connection_id": "not-a-uuid"}, {"connection_id": "../../etc"}, {}):
        assert world.client.get(CONNECTION, params=params,
                                headers=auth(READER_KEY)).status_code == 422  # fmt: skip
    missing = {"connection_id": str(uuid4())}
    for method, path in [("GET", CONNECTION), ("POST", TEST), ("POST", ENABLE),
                         ("DELETE", CONNECTION)]:  # fmt: skip
        response = world.client.request(method, path, params=missing, headers=auth(MANAGER_KEY))
        assert (response.status_code, response.json()["detail"]) == (
            404,
            "Integration connection not found",
        ), (method, path)


def test_driver_crash_and_timeout_are_classified_results(world: World) -> None:
    body = {"integration_id": "example-messaging", "display_name": "Notify", "credentials": {}}
    crash = world.client.post(
        CONNECTIONS, headers=auth(MANAGER_KEY), json=body | {"config": {"mode": "crash"}}
    )
    crash = crash.json()["connection"]
    result = world.client.post(TEST, params={"connection_id": crash["connection_id"]},
                               headers=auth(MANAGER_KEY))  # fmt: skip
    assert result.json()["connection"]["last_test_error"] == "provider_error"
    assert CRASH_MARKER not in result.text and CRASH_MARKER not in world.everything()
    hang = world.client.post(
        CONNECTIONS, headers=auth(MANAGER_KEY), json=body | {"config": {"mode": "hang"}}
    )
    hang = hang.json()["connection"]
    result = world.client.post(TEST, params={"connection_id": hang["connection_id"]},
                               headers=auth(MANAGER_KEY))  # fmt: skip
    assert result.json()["connection"]["last_test_error"] == "timeout"


def test_credentials_need_configured_secret_storage(settings, tmp_path: Path) -> None:
    world = World(settings, tmp_path, with_secret_store=False)
    refused = create(world)
    assert (refused.status_code, refused.json()["detail"]) == (
        503,
        "Integration secret storage unavailable",
    )
    assert world.repository.rows == {}
    plain = world.client.post(CONNECTIONS, headers=auth(MANAGER_KEY), json={
        "integration_id": "example-messaging", "display_name": "Notify", "config": {}})  # fmt: skip
    assert plain.status_code == 201 and plain.json()["connection"]["configured_secret_fields"] == []


# ----- catalog, OpenAPI and the default (production) build ---------------------------------------


def test_catalog_exposes_definitions_and_never_values(world: World) -> None:
    body = world.client.get(CATALOG, headers=auth(READER_KEY)).json()
    integrations = {i["integration_id"]: i for i in body["integrations"]}
    assert list(integrations) == ["example-commerce", "example-marketing", "example-messaging"]
    assert integrations["example-marketing"]["connectable"] is False
    kinds = {f["name"]: f["kind"] for f in integrations["example-commerce"]["fields"]}
    assert kinds["api_key"] == "secret" and kinds["store_url"] == "url"


def test_openapi_documents_the_integration_routes(world: World) -> None:
    spec = world.client.get("/openapi.json").json()
    paths = spec["paths"]
    expected = {CATALOG: {"get"}, CONNECTIONS: {"get", "post"},
                CONNECTION: {"get", "put", "delete"}, CREDENTIALS: {"put"}, TEST: {"post"},
                ENABLE: {"post"}, DISABLE: {"post"}}  # fmt: skip
    for path, methods in expected.items():
        assert set(paths[path]) >= methods, path
        for method in methods:
            operation = paths[path][method]
            assert operation["tags"] == ["integrations"] and operation["summary"]
            permission = "integrations.read" if method == "get" else "integrations.manage"
            assert f"`{permission}`" in operation["description"], (path, method)
    schemas = spec["components"]["schemas"]
    ours = [paths[p] for p in expected] + [schemas[name] for name in (
        "CatalogResponse", "ConnectionView", "ConnectionResponse", "ConnectionListResponse",
        "CreateConnectionRequest", "UpdateConnectionRequest", "ReplaceCredentialsRequest",
        "ConnectionDeletedResponse", "IntegrationDefinitionView")]  # fmt: skip
    text = json.dumps(ours)
    has_examples = '"example"' in text or '"examples"' in text
    assert not has_examples and SECRET not in text  # no (credential) examples documented
    assert "configured_secret_fields" in schemas["ConnectionView"]["properties"]
    credentials = schemas["CreateConnectionRequest"]["properties"]["credentials"]
    (value_schema,) = credentials["patternProperties"].values()
    assert value_schema["writeOnly"] is True and value_schema["format"] == "password"


def test_the_default_build_installs_no_integration(settings) -> None:
    composition = build_integration_management(settings)  # the real default catalog
    try:
        assert composition.service is not None
        from app.context.models import ActorContext, RequestContext

        actor = ActorContext(actor_id="a", actor_type="api_client", company_id="c",
                             permissions=frozenset({"integrations.read"}))  # fmt: skip
        assert composition.service.catalog(RequestContext(actor=actor)) == ()
    finally:
        composition.discard()
