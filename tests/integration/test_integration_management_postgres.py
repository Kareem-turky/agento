"""Task 031 on the real, migrated PostgreSQL: connection metadata persists (and survives a
new application), management mutations land in the existing audit trail, and no secret
VALUE ever reaches PostgreSQL. Deterministic fake definitions/drivers; no network.
"""

import socket
from pathlib import Path

import pytest
import sqlalchemy as sa
from agno.os.settings import AgnoAPISettings
from fastapi.testclient import TestClient

from app.composition.integrations import build_integration_management
from app.main import create_app
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.integration_fakes import VALID_KEY, fake_catalog
from tests.support.product_auth import deployment_settings, principal

pytestmark = pytest.mark.integration

MANAGER_KEY = "test-integrations-pg-manager-key-" + "m" * 20
SECRET = "test-only-pg-credential-" + "q" * 24  # noqa: S105 - test fixture
COMPANY = "test-integrations-company"
CONNECTIONS = "/api/v1/integrations/connections"
CONNECTION = "/api/v1/integrations/connection"
TEST = "/api/v1/integrations/connection/test"
CREDENTIALS = "/api/v1/integrations/connection/credentials"
HEADERS = {"Authorization": f"Bearer {MANAGER_KEY}"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_socket: object, address: object) -> None:
        raise AssertionError("unexpected outbound connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    path = tmp_path / "integration-secrets"
    path.mkdir(mode=0o700)
    return path


def app_for(settings, secrets_dir: Path):
    configured = deployment_settings(
        settings, "test", company_id=COMPANY, integration_secrets_dir=secrets_dir,
        product_api_keys=(principal(MANAGER_KEY, key_id="pg-manager", actor_id="pg-manager",
                          permissions=frozenset({"integrations.read", "integrations.manage"})),),
    )  # fmt: skip
    catalog, _ = fake_catalog()
    composition = build_integration_management(configured, catalog=catalog)

    async def close() -> None:
        await composition.close()

    runtime = AgnoAPISettings(os_security_key=TEST_OS_SECURITY_KEY)
    return create_app(configured, runtime, integration_service=composition.service,
                      shutdown_callback=close)  # fmt: skip


def product_dump(engine: sa.Engine) -> str:
    """Every row of every Product table, as text."""
    with engine.connect() as connection:
        tables = connection.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'product'"
        )).scalars().all()  # fmt: skip
        return "\n".join(
            str(connection.execute(sa.text(f'SELECT * FROM product."{t}"')).all())  # noqa: S608
            for t in tables
        )


def test_lifecycle_persists_metadata_audits_and_never_stores_secret_values(
    settings, migrated, engine: sa.Engine, secrets_dir: Path
) -> None:
    with TestClient(app_for(settings, secrets_dir)) as client:
        created = client.post(CONNECTIONS, headers=HEADERS, json={
            "integration_id": "example-commerce", "display_name": "PG store",
            "config": {"store_url": "https://pg.example.test"},
            "credentials": {"api_key": SECRET}})  # fmt: skip
        assert created.status_code == 201
        cid = created.json()["connection"]["connection_id"]
        params = {"connection_id": cid}
        failed = client.post(TEST, params=params, headers=HEADERS).json()["connection"]
        assert failed["last_test_result"] == "failure"
        replaced = client.put(CREDENTIALS, params=params, headers=HEADERS,
                              json={"credentials": {"api_key": VALID_KEY}})  # fmt: skip
        assert replaced.status_code == 200
        ok = client.post(TEST, params=params, headers=HEADERS).json()["connection"]
        assert ok["last_test_result"] == "success"

    with engine.connect() as connection:
        row = connection.execute(sa.text(
            "SELECT company_id, integration_id, config, secret_fields, enabled, "
            "last_test_result, last_test_error, last_tested_at IS NOT NULL AS tested "
            "FROM product.integration_connections WHERE connection_id = :c"), {"c": cid}
        ).mappings().one()  # fmt: skip
        audit = connection.execute(sa.text(
            "SELECT action_name, event_type, actor_id, company_id, store_id, verification_code "
            "FROM product.audit_events WHERE action_name LIKE 'integrations.%' "
            "AND company_id = :c ORDER BY occurred_at, event_id"), {"c": COMPANY}
        ).mappings().all()  # fmt: skip
    assert dict(row) == {
        "company_id": COMPANY, "integration_id": "example-commerce",
        "config": {"store_url": "https://pg.example.test"}, "secret_fields": ["api_key"],
        "enabled": True, "last_test_result": "success", "last_test_error": None, "tested": True,
    }  # fmt: skip
    verified = [(a["action_name"], a["verification_code"]) for a in audit
                if a["event_type"] == "verified"]  # fmt: skip
    assert verified == [
        ("integrations.connection.create", "connection_created"),
        ("integrations.connection.test", "connection_test_failed"),
        ("integrations.connection.credentials.replace", "credentials_replaced"),
        ("integrations.connection.test", "connection_test_succeeded"),
    ]
    assert {a["actor_id"] for a in audit} == {"pg-manager"} and {a["store_id"] for a in audit} == {
        None
    }
    # No secret VALUE anywhere in PostgreSQL (metadata, audit or any other Product table).
    dump = product_dump(engine)
    assert SECRET not in dump and VALID_KEY not in dump
    assert (secrets_dir / f"{cid}.json").exists()


def test_metadata_survives_a_new_application_and_delete_removes_secrets(
    settings, migrated, engine: sa.Engine, secrets_dir: Path
) -> None:
    with TestClient(app_for(settings, secrets_dir)) as client:
        cid = client.post(CONNECTIONS, headers=HEADERS, json={
            "integration_id": "example-commerce", "display_name": "Durable",
            "config": {"store_url": "https://durable.example.test"},
            "credentials": {"api_key": SECRET}}).json()["connection"]["connection_id"]  # fmt: skip
    with TestClient(app_for(settings, secrets_dir)) as client:  # a brand-new application
        again = client.get(CONNECTION, params={"connection_id": cid}, headers=HEADERS)
        assert again.status_code == 200
        assert again.json()["connection"]["configured_secret_fields"] == ["api_key"]
        deleted = client.delete(CONNECTION, params={"connection_id": cid}, headers=HEADERS)
        assert deleted.status_code == 200
    assert not (secrets_dir / f"{cid}.json").exists()
    with engine.connect() as connection:
        assert connection.execute(sa.text(
            "SELECT count(*) FROM product.integration_connections WHERE connection_id = :c"),
            {"c": cid}).scalar_one() == 0  # fmt: skip


def test_corrupted_metadata_fails_closed_without_leaking(
    settings, migrated, engine: sa.Engine, secrets_dir: Path
) -> None:
    with TestClient(app_for(settings, secrets_dir)) as client:
        cid = client.post(CONNECTIONS, headers=HEADERS, json={
            "integration_id": "example-messaging", "display_name": "Corrupt me",
            "config": {"mode": "fine"}}).json()["connection"]["connection_id"]  # fmt: skip
        marker = "CORRUPTED-ROW-MARKER-5531"
        with engine.begin() as connection:
            connection.execute(sa.text(
                "UPDATE product.integration_connections SET config = "
                "CAST(:v AS jsonb) WHERE connection_id = :c"),
                {"v": f'{{"password": "{marker}"}}', "c": cid})  # fmt: skip
        response = client.get(CONNECTION, params={"connection_id": cid}, headers=HEADERS)
        assert response.status_code == 503 and marker not in response.text
        listed = client.get(CONNECTIONS, headers=HEADERS)
        assert listed.status_code == 503 and marker not in listed.text
