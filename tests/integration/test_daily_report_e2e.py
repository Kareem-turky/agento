"""GET /api/v1/operations/reports/daily through the LOCAL/TEST mock deployment.

REAL and composed by ``create_deployment_app`` (nothing injected but the model the
mock composition requires): Product API-key auth, the report route and service,
DailyOperationsWorkflow, GovernanceGate and the deterministic MockCommerceAdapter,
next to PostgreSQL (migrated to 0002) command/audit persistence that must stay
untouched. The scripted model must receive ZERO requests: the report is not an agent.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.composition import local_mock
from app.integrations.commerce.mock import EntityType, canonical_id
from tests.conftest import TEST_OS_SECURITY_KEY
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.support.scripted_tool_model import ScriptedToolModel

pytestmark = pytest.mark.integration

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))
NORTH = str(canonical_id(EntityType.STORE, "shop_north"))
ORDER_2002 = str(canonical_id(EntityType.ORDER, "ord_2002"))
SHIP_507 = str(canonical_id(EntityType.SHIPMENT, "ship_507"))
PATH = "/api/v1/operations/reports/daily"
READS = frozenset({"stores.read", "orders.read", "shipments.read"})
NO_STORES_KEY = "test-product-key-nostores-" + "n" * 24  # noqa: S105 - test-only
PRODUCT = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
LIMITED = {"Authorization": f"Bearer {NO_STORES_KEY}"}
OS = {"Authorization": f"Bearer {TEST_OS_SECURITY_KEY}"}
PARAMS = {"store_id": SOUTH, "business_date": "2026-03-03"}
NEVER_IN_REPORT = (
    "ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express", "cus_005",
    "Robin Demo", "shop_south", "South Storefront", "acct_demo", COMPANY, "deploy-report-actor",
    "source_status", "external_refs", "tracking", "courier", "customer", "company_id",
    "actor_id", "permissions", "role", "@", "mock-commerce",
)  # fmt: skip


class Observed:
    def __init__(self) -> None:
        self.systems: list = []
        self.adapters: list = []
        self.desks: list = []
        self.workflows: list = []


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> Observed:
    seen = Observed()
    for name, bucket in (("MockCommerceSystem", seen.systems),
                         ("MockCommerceAdapter", seen.adapters),
                         ("MockTicketDesk", seen.desks),
                         ("DailyOperationsWorkflow", seen.workflows)):  # fmt: skip
        original = getattr(local_mock, name)

        def wrapper(*args, _original=original, _bucket=bucket, **kwargs):
            _bucket.append((_original(*args, **kwargs), args, kwargs))
            return _bucket[-1][0]

        monkeypatch.setattr(local_mock, name, wrapper)
    return seen


def report_deployment(settings):
    reader = principal(key_id="report-reader", actor_id="deploy-report-actor", permissions=READS,
                       store_ids=frozenset({SOUTH}))  # fmt: skip
    no_stores_read = principal(NO_STORES_KEY, key_id="no-stores-read", actor_id="limited",
                               permissions=READS - {"stores.read"},
                               store_ids=frozenset({SOUTH}))  # fmt: skip
    return deployment_settings(settings, "test", company_id=COMPANY, business_backend="mock",
                               product_api_keys=(reader, no_stores_read))  # fmt: skip


def counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        commands = connection.execute(sa.text("SELECT count(*) FROM product.write_commands"))
        audits = connection.execute(sa.text("SELECT count(*) FROM product.audit_events"))
        return commands.scalar_one(), audits.scalar_one()


def test_daily_report_over_the_mock_deployment(settings, runtime_settings, migrated, engine,
                                               observed) -> None:  # fmt: skip
    model = ScriptedToolModel()
    before = counts(engine)
    app = create_deployment_app(report_deployment(settings), runtime_settings, model=model)
    with TestClient(app) as client:
        # Auth matrix.
        assert client.get(PATH, params=PARAMS).status_code == 401
        wrong = {"Authorization": "Bearer test-wrong-key-" + "w" * 32}
        assert client.get(PATH, params=PARAMS, headers=wrong).status_code == 401
        assert client.get(PATH, params=PARAMS, headers=OS).status_code == 401
        assert client.get("/agents", headers=PRODUCT).status_code == 401
        assert client.get("/agents", headers=OS).status_code == 200
        # Exact store grant, and the missing stores.read permission.
        foreign = client.get(PATH, params=PARAMS | {"store_id": NORTH}, headers=PRODUCT)
        assert (foreign.status_code, foreign.json()) == (403, {"detail": "Forbidden"})
        limited = client.get(PATH, params=PARAMS, headers=LIMITED)
        assert (limited.status_code, limited.json()) == (403, {"detail": "Forbidden"})

        first = client.get(PATH, params=PARAMS, headers=PRODUCT)
        second = client.get(PATH, params=PARAMS, headers=PRODUCT)
        # (desk observed while the application is alive)
        (desk, _, _) = observed.desks[0]
        tickets = desk.ticket_count

    assert first.status_code == 200 and second.status_code == 200
    body = first.json()
    assert body["request_id"] == first.headers["X-Request-ID"]
    report = body["report"]
    assert (report["store_id"], report["business_date"], report["timezone"]) == (
        SOUTH, "2026-03-03", "Europe/Berlin",
    )  # fmt: skip
    assert (report["window_start"], report["window_end"]) == (
        "2026-03-03T00:00:00+01:00", "2026-03-04T00:00:00+01:00",
    )  # fmt: skip
    metrics = report["metrics"]
    assert (metrics["orders_created"], metrics["shipments_shipped"],
            metrics["affected_orders"]) == (1, 1, 1)  # fmt: skip
    assert {c["status"]: c["count"] for c in metrics["order_status_counts"]}["processing"] == 1
    assert sum(c["count"] for c in metrics["order_status_counts"]) == 1
    assert {c["status"]: c["count"] for c in metrics["shipment_status_counts"]}["failed"] == 1
    assert sum(c["count"] for c in metrics["shipment_status_counts"]) == 1
    assert report["findings"] == [{
        "code": "shipment_failed", "severity": "critical", "entity_type": "shipment",
        "entity_id": SHIP_507, "order_id": ORDER_2002, "canonical_status": "failed",
        "recommended_action": "review_failed_shipment",
    }]  # fmt: skip
    assert (report["findings_total"], report["findings_truncated"]) == (1, False)
    assert report["coverage"] == {
        "orders": "created_in_business_day", "shipments": "shipped_in_business_day",
        "inventory": "not_included",
        "inventory_reason": "store_scoped_inventory_query_unavailable",
    }  # fmt: skip

    # Provider data and PII never leave; canonical UUIDs are the only identifiers.
    for leak in NEVER_IN_REPORT:
        assert leak not in first.text, leak

    # Deterministic: identical report apart from generated_at; new request id.
    again = second.json()
    assert again["request_id"] != body["request_id"]
    assert {**again["report"], "generated_at": None} == {**report, "generated_at": None}

    # Read-only: no command, no audit lifecycle, no ticket, no model call.
    assert counts(engine) == before
    assert tickets == 0
    assert model.requests == []

    # The report reads through the SAME adapter/gate the Operations Agent uses.
    assert len(observed.systems) == 1 and len(observed.adapters) == 1
    (workflow, _, kwargs) = observed.workflows[0]
    assert kwargs["commerce"] is observed.adapters[0][0]
    assert observed.adapters[0][1] == (observed.systems[0][0],)


def test_report_today_uses_the_store_timezone_through_the_deployment(
    settings, runtime_settings, migrated
) -> None:
    app = create_deployment_app(report_deployment(settings), runtime_settings,
                                model=ScriptedToolModel())  # fmt: skip
    with TestClient(app) as client:
        response = client.get(PATH, params={"store_id": SOUTH}, headers=PRODUCT)
    assert response.status_code == 200
    report = response.json()["report"]
    # No date: the store-local "today" of the real clock (whatever it is).
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    local_today = datetime.now(ZoneInfo("Europe/Berlin")).date()
    assert date.fromisoformat(report["business_date"]) in (
        local_today,
        local_today.fromordinal(local_today.toordinal() - 1),
    )  # tolerate a midnight boundary between the call and this assertion
    assert report["timezone"] == "Europe/Berlin"
