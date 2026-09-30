"""Task 030: the REAL Operations Agent on the LOCAL-DEMO-ONLY model, selected by
configuration (``APP_DEFAULT_MODEL_PROVIDER=demo``), not by the ``model=`` test seam.

Real ``create_deployment_app``, mock business backend, real Product API-key auth, real
migrated PostgreSQL. The demo model asks the real Daily Operations Report tool and only
formats its result; the analysis surface stays read-only; no outbound connection.
"""

import socket

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.runtime.demo_model import USAGE_HINT
from tests.support.canonical_mock import AGENT_NEVER_SHOWS, BUSINESS_DATE, COMPANY, SOUTH
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal

pytestmark = pytest.mark.integration

RUNS = "/api/v1/operations/runs"
HEADERS = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
EXPECTED_SUMMARY = (
    "Daily operations report for 2026-03-03 (Europe/Berlin).\n"
    "Orders created: 1. Shipments shipped: 1. Affected orders: 1.\n"
    "Shipment statuses: failed 1.\n"
    "Findings: 1.\n"
    "- critical: shipment_failed (shipment status failed); recommended action: "
    "review_failed_shipment.\n"
    "Inventory: not_included (store_scoped_inventory_query_unavailable)."
)


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def refuse(_socket: object, address: object) -> None:
        attempts.append(address)
        raise AssertionError(f"unexpected outbound connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return attempts


def demo_settings(settings, environment: str):
    operator = principal(
        key_id="demo-operator", actor_id="demo-operator",
        permissions=frozenset({"stores.read", "orders.read", "shipments.read", "tickets.create"}),
        store_ids=frozenset({SOUTH}),
    )  # fmt: skip
    return deployment_settings(
        settings, environment, company_id=COMPANY, business_backend="mock",
        product_api_keys=(operator,), default_model_provider="demo", default_model_id=None,
    )  # fmt: skip


def write_counts(engine: sa.Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        return (
            connection.execute(sa.text("SELECT count(*) FROM product.write_commands")).scalar_one(),
            connection.execute(sa.text("SELECT count(*) FROM product.audit_events")).scalar_one(),
        )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_demo_model_analysis_through_the_product_api(
    settings, runtime_settings, migrated, engine, no_outbound_network, environment
) -> None:
    app = create_deployment_app(demo_settings(settings, environment), runtime_settings)
    with TestClient(app) as client:
        before = write_counts(engine)
        first = client.post(RUNS, headers=HEADERS,
                            json={"message": f"Analyze operations for {BUSINESS_DATE}.",
                                  "store_id": SOUTH})  # fmt: skip
        again = client.post(RUNS, headers=HEADERS,
                            json={"message": f"Analyze operations for {BUSINESS_DATE}.",
                                  "store_id": SOUTH})  # fmt: skip
        after = write_counts(engine)

    assert first.status_code == again.status_code == 200
    assert first.json()["message"] == again.json()["message"] == EXPECTED_SUMMARY
    for marker in AGENT_NEVER_SHOWS:
        assert marker not in first.text, marker
    assert after == before  # read-only
    assert no_outbound_network == []


def test_demo_model_never_turns_analysis_into_a_write(
    settings, runtime_settings, migrated, engine, no_outbound_network
) -> None:
    app = create_deployment_app(demo_settings(settings, "local"), runtime_settings)
    with TestClient(app) as client:
        before = write_counts(engine)
        run = client.post(RUNS, headers=HEADERS, json={
            "message": f"Analyze operations for {BUSINESS_DATE} and create a ticket.",
            "store_id": SOUTH})  # fmt: skip
        undated = client.post(
            RUNS, headers=HEADERS, json={"message": "Create a ticket now.", "store_id": SOUTH}
        )
        after = write_counts(engine)

    assert run.status_code == 200 and run.json()["message"] == EXPECTED_SUMMARY
    assert undated.status_code == 200 and undated.json()["message"] == USAGE_HINT
    assert after == before  # no write command, no audit lifecycle
    assert no_outbound_network == []
