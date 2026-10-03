"""GET /api/v1/operations/reports/daily: transport behaviour.

Real FastAPI app, RequestContextMiddleware and AgentOS auth layer. The report comes
either from a recording fake service (transport rules) or from the REAL
DailyOperationsWorkflow over the deterministic mock adapter (no model anywhere).
"""

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsUnavailableError,
)
from tests.support.actor_resolver import StaticActorResolver
from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal
from tests.workflows.helpers import COMPANY, NORTH, SOUTH, SpyCommerce, actor, workflow

PATH = "/api/v1/operations/reports/daily"
UNAVAILABLE = {"detail": "Daily operations report unavailable"}
READER = actor(store_ids=frozenset({SOUTH}))


class FakeService:
    def __init__(self, returns=None, raises: BaseException | None = None) -> None:
        self.returns, self.raises = returns, raises
        self.calls: list[tuple] = []

    async def get_daily_report(self, request, scope, business_date):
        self.calls.append((request, scope, business_date))
        if self.raises is not None:
            raise self.raises
        return self.returns


def build(settings, runtime_settings, service=None, who=READER, **kwargs):
    return create_app(settings, runtime_settings, actor_resolver=StaticActorResolver(who),
                      daily_operations_service=service, **kwargs)  # fmt: skip


def real_service(commerce=None):
    return workflow(commerce or SpyCommerce())


def test_real_workflow_report_over_http(settings, runtime_settings) -> None:
    commerce = SpyCommerce()
    app = build(settings, runtime_settings, real_service(commerce))
    with TestClient(app) as client:
        response = client.get(PATH, params={"store_id": SOUTH, "business_date": "2026-03-03"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"request_id", "report"}
    assert body["request_id"] == response.headers["X-Request-ID"]
    report = body["report"]
    assert set(report) == {
        "store_id", "business_date", "timezone", "window_start", "window_end", "generated_at",
        "metrics", "findings", "findings_total", "findings_truncated", "coverage",
    }  # fmt: skip
    assert (report["store_id"], report["business_date"], report["timezone"]) == (
        SOUTH, "2026-03-03", "Europe/Berlin",
    )  # fmt: skip
    assert report["window_start"] == "2026-03-03T00:00:00+01:00"
    assert report["window_end"] == "2026-03-04T00:00:00+01:00"
    assert report["metrics"]["orders_created"] == 1
    assert report["metrics"]["shipments_shipped"] == 1
    assert [f["code"] for f in report["findings"]] == ["shipment_failed"]
    assert report["coverage"]["inventory"] == "not_included"
    for leak in ("ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express",
                 "cus_005", "Robin Demo", "shop_south", "acct_demo", COMPANY, "source_status",
                 "external_refs", "tracking", "actor_id", "company_id", "permissions",
                 "report-reader", "stores.read"):  # fmt: skip
        assert leak not in response.text, leak


def test_trusted_scope_reaches_the_service(settings, runtime_settings) -> None:
    service = FakeService(raises=DailyOperationsUnavailableError())
    with TestClient(build(settings, runtime_settings, service)) as client:
        client.get(PATH, params={"store_id": SOUTH})
        client.get(PATH, params={"store_id": SOUTH, "business_date": "2026-03-03"})
    (req1, scope1, date1), (_, _, date2) = service.calls
    assert req1.actor == READER
    assert (scope1.company_id, scope1.store_id) == (COMPANY, SOUTH)
    assert (date1, date2) == (None, date(2026, 3, 3))


def test_ungranted_store_is_forbidden_before_the_service(settings, runtime_settings) -> None:
    service = FakeService()
    with TestClient(build(settings, runtime_settings, service)) as client:
        response = client.get(PATH, params={"store_id": NORTH})
    assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})
    assert service.calls == []


@pytest.mark.parametrize("missing", ["stores.read", "orders.read", "shipments.read"])
def test_missing_report_permission_is_403_with_no_integration_call(
    settings, runtime_settings, missing
) -> None:
    commerce = SpyCommerce()
    who = actor(
        store_ids=frozenset({SOUTH}),
        permissions=frozenset({"stores.read", "orders.read", "shipments.read"}) - {missing},
    )
    app = build(settings, runtime_settings, real_service(commerce), who=who)
    with TestClient(app) as client:
        response = client.get(PATH, params={"store_id": SOUTH, "business_date": "2026-03-03"})
    assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})
    assert commerce.calls == []


@pytest.mark.parametrize(
    "outcome",
    [
        DailyOperationsForbiddenError(),
        DailyOperationsUnavailableError(),
        RuntimeError("database credential=SECRETMARKER at 10.0.0.1"),
        ValueError("bad"),
    ],
)
def test_service_errors_are_fixed_and_safe(settings, runtime_settings, outcome) -> None:
    with TestClient(build(settings, runtime_settings, FakeService(raises=outcome))) as client:
        response = client.get(PATH, params={"store_id": SOUTH})
    if isinstance(outcome, DailyOperationsForbiddenError):
        assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})
    else:
        assert (response.status_code, response.json()) == (503, UNAVAILABLE)
    assert "SECRETMARKER" not in response.text


@pytest.mark.parametrize("bad", [None, {"store_id": SOUTH}, "report", 42])
def test_broken_service_results_are_503(settings, runtime_settings, bad) -> None:
    with TestClient(build(settings, runtime_settings, FakeService(returns=bad))) as client:
        response = client.get(PATH, params={"store_id": SOUTH})
    assert (response.status_code, response.json()) == (503, UNAVAILABLE)


def test_unconfigured_service_is_503(settings, runtime_settings) -> None:
    with TestClient(build(settings, runtime_settings, None)) as client:
        response = client.get(PATH, params={"store_id": SOUTH})
    assert (response.status_code, response.json()) == (503, UNAVAILABLE)


def test_invalid_store_timezone_is_503_and_not_echoed(settings, runtime_settings) -> None:
    from tests.workflows.helpers import FakeCommerce, make_store

    fake = FakeCommerce(store=make_store(tz="Mars/Olympus_Mons"))
    with TestClient(build(settings, runtime_settings, workflow(fake))) as client:
        response = client.get(PATH, params={"store_id": SOUTH, "business_date": "2026-03-03"})
    assert (response.status_code, response.json()) == (503, UNAVAILABLE)
    assert "Mars" not in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"store_id": SOUTH, "timezone": "UTC"},
        {"store_id": SOUTH, "company_id": COMPANY},
        {"store_id": SOUTH, "actor_id": "admin"},
        {"store_id": SOUTH, "permissions": "*"},
        {"store_id": SOUTH, "findings": "none"},
        [("store_id", SOUTH), ("store_id", NORTH)],
        [("store_id", SOUTH), ("business_date", "2026-03-03"), ("business_date", "2026-03-04")],
    ],
)
def test_only_documented_query_parameters_are_accepted(settings, runtime_settings, params):
    service = FakeService()
    with TestClient(build(settings, runtime_settings, service)) as client:
        response = client.get(PATH, params=params)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "params",
    [{}, {"store_id": "not-a-uuid"}, {"store_id": SOUTH, "business_date": "03/03/2026"},
     {"store_id": SOUTH, "business_date": "2026-02-30"},
     {"store_id": SOUTH, "business_date": "2026-03-03T10:00:00"}],
)  # fmt: skip
def test_malformed_parameters_are_422(settings, runtime_settings, params) -> None:
    service = FakeService()
    with TestClient(build(settings, runtime_settings, service)) as client:
        assert client.get(PATH, params=params).status_code == 422
    assert service.calls == []


def test_the_route_is_get_only(settings, runtime_settings) -> None:
    with TestClient(build(settings, runtime_settings, FakeService())) as client:
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(PATH).status_code == 405


def test_product_and_agentos_credentials_stay_separate(settings, runtime_settings,
                                                       auth_headers) -> None:  # fmt: skip
    grant = principal(
        store_ids=frozenset({SOUTH}),
        permissions=frozenset({"stores.read", "orders.read", "shipments.read"}),
    )
    s = deployment_settings(settings, "test", company_id=COMPANY, product_api_keys=(grant,))
    app = create_app(s, runtime_settings, daily_operations_service=real_service())
    params = {"store_id": SOUTH, "business_date": "2026-03-03"}
    product = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
    with TestClient(app) as client:
        assert client.get(PATH, params=params).status_code == 401
        wrong = {"Authorization": "Bearer test-wrong-key-" + "w" * 32}
        assert client.get(PATH, params=params, headers=wrong).status_code == 401
        assert client.get(PATH, params=params, headers=auth_headers).status_code == 401  # OS key
        assert client.get(PATH, params=params, headers=product).status_code == 200
        assert client.get("/agents", headers=product).status_code == 401
        assert client.get("/agents", headers=auth_headers).status_code == 200
    excluded = app.state.agent_os.authorization_config.excluded_route_paths
    assert PATH in excluded and not [p for p in excluded if any(c in p for c in "*?[{")]
    assert UUID(SOUTH)


# ----- safe validation answers (SafeValidationRoute) ---------------------------------------------

SAFE_KEYS = {"type", "loc", "msg"}
MARKER = "SUBMITTED-REPORT-MARKER-4b2e1d"


@pytest.mark.parametrize(
    "params",
    [{"store_id": MARKER}, {"store_id": SOUTH, "business_date": MARKER},
     {"store_id": SOUTH, "business_date": f"2026-02-30{MARKER}"}],
)  # fmt: skip
def test_malformed_parameters_never_echo_the_submitted_value(
    settings, runtime_settings, params
) -> None:
    service = FakeService()
    with TestClient(build(settings, runtime_settings, service)) as client:
        response = client.get(PATH, params=params)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail and all(set(entry) == SAFE_KEYS for entry in detail)
    assert MARKER not in response.text and '"input"' not in response.text
    assert service.calls == []


def test_unsupported_parameters_keep_their_fixed_answer(settings, runtime_settings) -> None:
    service = FakeService()
    with TestClient(build(settings, runtime_settings, service)) as client:
        response = client.get(PATH, params={"store_id": SOUTH, "company_id": MARKER})
    assert (response.status_code, response.json()) == (
        422, {"detail": "Unsupported query parameters"})  # fmt: skip
    assert service.calls == []
