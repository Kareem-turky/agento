"""DailyOperationsWorkflow: governance preflight, store-local business day, strict
validation of untrusted integration output, deterministic metrics and findings."""

import json
import random
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.integrations.commerce import IntegrationUnavailableError
from app.integrations.commerce.mock import EntityType, canonical_id
from app.services.operations_reports import (
    MAX_DAILY_FINDINGS,
    DailyOperationsForbiddenError,
    DailyOperationsReport,
    DailyOperationsUnavailableError,
)
from app.workflows import business_day_window
from tests.workflows.helpers import (
    COMPANY,
    FIXED_NOW,
    NORTH,
    SOUTH,
    FakeCommerce,
    SpyCommerce,
    make_order,
    make_shipment,
    make_store,
    request,
    run,
    scope,
    workflow,
)

BERLIN = ZoneInfo("Europe/Berlin")
MARCH_3 = date(2026, 3, 3)
ORDER_2002 = canonical_id(EntityType.ORDER, "ord_2002")
SHIP_507 = canonical_id(EntityType.SHIPMENT, "ship_507")


def counts(report: DailyOperationsReport, kind: str) -> dict[str, int]:
    return {c.status.value: c.count for c in getattr(report.metrics, f"{kind}_status_counts")}


# ----- the mock dataset: shop_south, 2026-03-03 ----------------------------------------


def test_shop_south_business_day_report() -> None:
    commerce = SpyCommerce()
    report = run(workflow(commerce).get_daily_report(request(), scope(SOUTH), MARCH_3))

    assert report.store_id == UUID(SOUTH)
    assert (report.business_date, report.timezone) == (MARCH_3, "Europe/Berlin")
    assert report.window_start == datetime(2026, 3, 3, tzinfo=BERLIN)
    assert report.window_end == datetime(2026, 3, 4, tzinfo=BERLIN)
    assert report.generated_at == FIXED_NOW
    m = report.metrics
    assert (m.orders_created, m.shipments_shipped, m.affected_orders) == (1, 1, 1)
    assert counts(report, "order") == {s.value: int(s is OrderStatus.PROCESSING)
                                       for s in OrderStatus}  # fmt: skip
    assert counts(report, "shipment") == {s.value: int(s is ShipmentStatus.FAILED)
                                          for s in ShipmentStatus}  # fmt: skip
    (finding,) = report.findings
    assert finding.model_dump(mode="json") == {
        "code": "shipment_failed", "severity": "critical", "entity_type": "shipment",
        "entity_id": str(SHIP_507), "order_id": str(ORDER_2002), "canonical_status": "failed",
        "recommended_action": "review_failed_shipment",
    }  # fmt: skip
    assert (report.findings_total, report.findings_truncated) == (1, False)
    assert report.coverage.model_dump(mode="json") == {
        "orders": "created_in_business_day", "shipments": "shipped_in_business_day",
        "inventory": "not_included",
        "inventory_reason": "store_scoped_inventory_query_unavailable",
    }  # fmt: skip

    # Governed, store/day-scoped queries; no inventory read, no provider filters.
    names = [c[0] for c in commerce.calls]
    assert names == ["get_store", "list_orders", "list_shipments"]  # parent is a day order
    order_query, shipment_query = commerce.calls[1][1], commerce.calls[2][1]
    assert (order_query.store_id, order_query.created_from, order_query.created_to) == (
        UUID(SOUTH), report.window_start, report.window_end,
    )  # fmt: skip
    assert order_query.statuses == frozenset() and order_query.limit is None
    assert (shipment_query.store_id, shipment_query.shipped_from, shipment_query.shipped_to) == (
        UUID(SOUTH), report.window_start, report.window_end,
    )  # fmt: skip
    assert shipment_query.order_id is None and shipment_query.limit is None

    text = report.model_dump_json()
    for leak in ("ord_2002", "ship_507", "delivery_failed", "SE000507", "Sample Express",
                 "cus_005", "shop_south", "acct_demo", COMPANY, "source_status",
                 "external_refs", "tracking"):  # fmt: skip
        assert leak not in text, leak


def test_parent_orders_outside_the_day_are_read_and_checked() -> None:
    """A shipment shipped today may belong to an older order: it is fetched once."""
    commerce = SpyCommerce()
    # 2026-03-05 in Berlin: ship_505 (ord_2003, created 03-04) and ship_509 (ord_2002).
    report = run(workflow(commerce).get_daily_report(request(), scope(SOUTH), date(2026, 3, 5)))
    assert report.metrics.orders_created == 0 and report.metrics.shipments_shipped == 2
    fetched = sorted(c[1] for c in commerce.calls if c[0] == "get_order")
    assert fetched == sorted([canonical_id(EntityType.ORDER, "ord_2002"),
                              canonical_id(EntityType.ORDER, "ord_2003")])  # fmt: skip


def test_north_store_uses_its_own_timezone() -> None:
    report = run(workflow().get_daily_report(request(), scope(NORTH), MARCH_3))
    ny = ZoneInfo("America/New_York")
    assert report.timezone == "America/New_York"
    assert report.window_start == datetime(2026, 3, 3, tzinfo=ny)
    assert report.window_end == datetime(2026, 3, 4, tzinfo=ny)
    # ord_1003 (08:15Z = 03:15 NY) and ord_1004 (14:45 NY) were created that day.
    assert report.metrics.orders_created == 2


# ----- today / timezone / DST ------------------------------------------------------------


def test_omitted_date_is_store_local_today_not_utc() -> None:
    # 23:30 UTC on 2026-03-03 is already 2026-03-04 in Berlin ...
    late = datetime(2026, 3, 3, 23, 30, tzinfo=UTC)
    report = run(workflow(clock=lambda: late).get_daily_report(request(), scope(SOUTH), None))
    assert report.business_date == date(2026, 3, 4) != late.date()
    assert report.window_start == datetime(2026, 3, 4, tzinfo=BERLIN)
    # ... and 03:00 UTC on 2026-03-04 is still 2026-03-03 in New York.
    early = datetime(2026, 3, 4, 3, 0, tzinfo=UTC)
    report = run(workflow(clock=lambda: early).get_daily_report(request(), scope(NORTH), None))
    assert report.business_date == date(2026, 3, 3) != early.date()
    # A clock in another offset changes nothing: the store's zone decides.
    tokyo = early.astimezone(ZoneInfo("Asia/Tokyo"))
    same = run(workflow(clock=lambda: tokyo).get_daily_report(request(), scope(NORTH), None))
    assert same.business_date == date(2026, 3, 3)


def test_naive_clock_fails_closed_before_any_read() -> None:
    commerce = SpyCommerce()
    with pytest.raises(DailyOperationsUnavailableError):
        run(workflow(commerce, clock=lambda: datetime(2026, 3, 3, 12)).get_daily_report(
            request(), scope(SOUTH), None))  # fmt: skip
    assert commerce.calls == []


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25), (date(2026, 3, 3), 24)],
)
def test_business_day_uses_local_midnights_across_dst(day, hours) -> None:
    start, end = business_day_window(day, BERLIN)
    assert (start.date(), start.hour, start.minute) == (day, 0, 0)
    assert (end.date(), end.hour, end.minute) == (day + timedelta(days=1), 0, 0)
    assert (start.astimezone(UTC), end.astimezone(UTC)) == (
        datetime.combine(day, datetime.min.time(), BERLIN).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), datetime.min.time(), BERLIN).astimezone(UTC),
    )  # fmt: skip
    # The real elapsed length of the local day (23h/25h on DST changes).
    assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=hours)


def test_dst_day_window_reaches_the_integration_and_bounds_records() -> None:
    day = date(2026, 3, 29)  # Berlin springs forward: 23-hour business day
    inside = make_order(datetime(2026, 3, 29, 23, 30, tzinfo=BERLIN))  # 21:30Z
    fake = FakeCommerce(store=make_store(), orders=(inside,))
    report = run(workflow(fake).get_daily_report(request(), scope(SOUTH), day))
    assert report.metrics.orders_created == 1
    query = fake.calls[1][1]
    assert query.created_from.astimezone(UTC) == datetime(2026, 3, 28, 23, tzinfo=UTC)
    assert query.created_to.astimezone(UTC) == datetime(2026, 3, 29, 22, tzinfo=UTC)
    # 00:30 local on the next day is outside: returning it breaks the contract.
    outside = make_order(datetime(2026, 3, 30, 0, 30, tzinfo=BERLIN))
    with pytest.raises(DailyOperationsUnavailableError):
        run(workflow(FakeCommerce(store=make_store(), orders=(outside,))).get_daily_report(
            request(), scope(SOUTH), day))  # fmt: skip


@pytest.mark.parametrize("tz", ["Not/AZone", "../../etc/passwd", "UTC+99", " "])
def test_invalid_store_timezone_is_unavailable_and_not_echoed(tz) -> None:
    store = make_store(tz=tz) if tz.strip() else make_store()
    if not tz.strip():
        store = store.model_copy(update={"timezone": tz})
    fake = FakeCommerce(store=store)
    with pytest.raises(DailyOperationsUnavailableError) as info:
        run(workflow(fake).get_daily_report(request(), scope(SOUTH), MARCH_3))
    assert str(info.value) == "daily_operations_unavailable"
    assert not tz.strip() or tz.strip() not in repr(info.value)
    assert info.value.__cause__ is None
    assert [c[0] for c in fake.calls] == ["get_store"]  # nothing else was read


# ----- governance preflight ---------------------------------------------------------------


@pytest.mark.parametrize("missing", ["stores.read", "orders.read", "shipments.read"])
def test_each_missing_permission_is_forbidden_before_any_read(missing) -> None:
    commerce = SpyCommerce()
    permissions = frozenset({"stores.read", "orders.read", "shipments.read"}) - {missing}
    with pytest.raises(DailyOperationsForbiddenError):
        run(workflow(commerce).get_daily_report(request(permissions=permissions), scope(SOUTH),
                                                MARCH_3))  # fmt: skip
    assert commerce.calls == []


@pytest.mark.parametrize(
    "req_scope",
    [
        lambda: (request(store_ids=frozenset({NORTH})), scope(SOUTH)),  # store not granted
        lambda: (request(), scope(SOUTH, company="another-company")),  # other company
        lambda: (request(permissions=frozenset({"*"})), scope(SOUTH)),  # no wildcards
        lambda: (request(permissions=frozenset({"stores.read", "orders.read", "shipments.read",
                                                "tickets.create"}),
                         store_ids=frozenset()), scope(SOUTH)),
    ],
)  # fmt: skip
def test_governance_denials_read_nothing(req_scope) -> None:
    commerce = SpyCommerce()
    req, target = req_scope()
    with pytest.raises(DailyOperationsForbiddenError):
        run(workflow(commerce).get_daily_report(req, target, MARCH_3))
    assert commerce.calls == []


def test_anonymous_or_unscoped_calls_fail_closed() -> None:
    from app.context.models import RequestContext
    from app.governance import ActionScope

    commerce = SpyCommerce()
    with pytest.raises(DailyOperationsForbiddenError):
        run(workflow(commerce).get_daily_report(RequestContext(), scope(SOUTH), MARCH_3))
    with pytest.raises(DailyOperationsForbiddenError):
        run(workflow(commerce).get_daily_report(request(), ActionScope(company_id=COMPANY),
                                                MARCH_3))  # fmt: skip
    for bad_request, bad_scope, bad_date in (
        ({"actor": "x"}, scope(SOUTH), MARCH_3),
        (request(), {"store_id": SOUTH}, MARCH_3),
        (request(), scope(SOUTH), "2026-03-03"),
        (request(), scope(SOUTH), datetime(2026, 3, 3, tzinfo=UTC)),
    ):
        with pytest.raises(DailyOperationsUnavailableError):
            run(workflow(commerce).get_daily_report(bad_request, bad_scope, bad_date))
    assert commerce.calls == []


# ----- untrusted integration output: fail closed ---------------------------------------------


def day_at(hour: int) -> datetime:
    return datetime(2026, 3, 3, hour, tzinfo=BERLIN)


def broken_cases():
    other_store = str(canonical_id(EntityType.STORE, "shop_north"))
    order = make_order(day_at(9))
    foreign_order = make_order(day_at(9), store=other_store)
    old_foreign_parent = make_order(datetime(2026, 2, 1, tzinfo=BERLIN), store=other_store)
    return {
        "foreign store": FakeCommerce(store=make_store(store_id=other_store)),
        "foreign company": FakeCommerce(store=make_store(company=str(UUID(int=7)))),
        "store is not a Store": FakeCommerce(store={"id": SOUTH}),
        "store read fails": FakeCommerce(store=IntegrationUnavailableError("mock")),
        "foreign order": FakeCommerce(store=make_store(), orders=(foreign_order,)),
        "order before window": FakeCommerce(store=make_store(), orders=(
            make_order(datetime(2026, 3, 2, 23, 59, tzinfo=BERLIN)),)),
        "order at window end": FakeCommerce(store=make_store(), orders=(
            make_order(datetime(2026, 3, 4, 0, 0, tzinfo=BERLIN)),)),
        "duplicate order": FakeCommerce(store=make_store(), orders=(order, order)),
        "orders not a tuple": FakeCommerce(store=make_store(), orders=[order]),
        "order is a dict": FakeCommerce(store=make_store(), orders=({"id": "x"},)),
        "shipment of foreign store": FakeCommerce(
            store=make_store(), shipments=(make_shipment(old_foreign_parent, day_at(10)),),
            extra_orders={old_foreign_parent.id: old_foreign_parent}),
        "unshipped shipment": FakeCommerce(store=make_store(), orders=(order,),
                                           shipments=(make_shipment(order, None),)),
        "shipment after window": FakeCommerce(store=make_store(), orders=(order,), shipments=(
            make_shipment(order, datetime(2026, 3, 4, 0, 1, tzinfo=BERLIN)),)),
        "shipment with missing parent": FakeCommerce(
            store=make_store(), shipments=(make_shipment(old_foreign_parent, day_at(10)),)),
        "parent with another id": FakeCommerce(
            store=make_store(), shipments=(make_shipment(old_foreign_parent, day_at(10)),),
            extra_orders={old_foreign_parent.id: order}),
        "duplicate shipment": FakeCommerce(store=make_store(), orders=(order,), shipments=(
            (s := make_shipment(order, day_at(11))), s)),
    }  # fmt: skip


@pytest.mark.parametrize("case", sorted(broken_cases()))
def test_broken_integration_output_fails_closed(case) -> None:
    fake = broken_cases()[case]
    with pytest.raises(DailyOperationsUnavailableError) as info:
        run(workflow(fake).get_daily_report(request(), scope(SOUTH), MARCH_3))
    assert str(info.value) == "daily_operations_unavailable"
    assert info.value.__cause__ is None


# ----- findings, ordering, truncation, determinism ----------------------------------------


def test_only_the_four_rules_produce_findings() -> None:
    orders = tuple(make_order(day_at(9), status=s) for s in OrderStatus)
    parent = orders[0]
    shipments = tuple(make_shipment(parent, day_at(10), status=s) for s in ShipmentStatus)
    report = run(workflow(FakeCommerce(store=make_store(), orders=orders, shipments=shipments))
                 .get_daily_report(request(), scope(SOUTH), MARCH_3))  # fmt: skip
    got = [(f.code.value, f.severity.value, f.entity_type.value, f.canonical_status,
            f.recommended_action.value) for f in report.findings]  # fmt: skip
    assert got == [
        ("shipment_failed", "critical", "shipment", "failed", "review_failed_shipment"),
        ("order_status_unknown", "warning", "order", "unknown", "review_status_mapping"),
        ("shipment_returned", "warning", "shipment", "returned", "review_returned_shipment"),
        ("shipment_status_unknown", "warning", "shipment", "unknown", "review_status_mapping"),
    ]
    # Pending/processing/cancelled orders and pending/cancelled shipments are not findings.
    assert report.metrics.orders_created == len(OrderStatus)
    assert report.metrics.shipments_shipped == len(ShipmentStatus)
    unknown_order = next(o for o in orders if o.status is OrderStatus.UNKNOWN)
    assert report.metrics.affected_orders == len({parent.id, unknown_order.id})
    for finding in report.findings:
        if finding.entity_type.value == "shipment":
            assert finding.order_id == parent.id


def test_findings_are_sorted_and_truncated_over_the_full_set() -> None:
    orders = tuple(make_order(day_at(8)) for _ in range(30))
    shipments = [make_shipment(orders[i % 30], day_at(10), status=ShipmentStatus.RETURNED)
                 for i in range(80)]  # fmt: skip
    shipments += [make_shipment(orders[i % 30], day_at(11), status=ShipmentStatus.FAILED)
                  for i in range(70)]  # fmt: skip
    base = FakeCommerce(store=make_store(), orders=orders, shipments=tuple(shipments))
    report = run(workflow(base).get_daily_report(request(), scope(SOUTH), MARCH_3))
    assert report.findings_total == 150 and report.findings_truncated is True
    assert len(report.findings) == MAX_DAILY_FINDINGS == 100
    assert report.metrics.shipments_shipped == 150  # metrics are never truncated
    # All 70 critical findings come first, then the first 30 warnings in stable order.
    assert [f.code.value for f in report.findings] == ["shipment_failed"] * 70 + [
        "shipment_returned"] * 30  # fmt: skip
    keys = [(f.code.value, str(f.order_id), str(f.entity_id)) for f in report.findings[:70]]
    assert keys == sorted(keys)
    # Provider ordering cannot change the result.
    shuffled = list(shipments)
    random.Random(7).shuffle(shuffled)  # noqa: S311 - deterministic test order
    other = FakeCommerce(store=make_store(), orders=tuple(reversed(orders)),
                         shipments=tuple(shuffled))  # fmt: skip
    again = run(workflow(other).get_daily_report(request(), scope(SOUTH), MARCH_3))
    assert again == report


def test_same_inputs_and_clock_give_an_identical_report() -> None:
    first = run(workflow().get_daily_report(request(), scope(SOUTH), MARCH_3))
    second = run(workflow().get_daily_report(request(), scope(SOUTH), MARCH_3))
    assert first == second
    assert json.loads(first.model_dump_json()) == json.loads(second.model_dump_json())
    later = run(workflow(clock=lambda: FIXED_NOW + timedelta(hours=1)).get_daily_report(
        request(), scope(SOUTH), MARCH_3))  # fmt: skip
    assert later.model_copy(update={"generated_at": first.generated_at}) == first


def test_report_models_reject_inconsistent_content() -> None:
    report = run(workflow().get_daily_report(request(), scope(SOUTH), MARCH_3))
    data = report.model_dump()
    for change in (
        {"findings_total": 2},
        {"findings_truncated": True},
        {"window_end": report.window_start},
        {"findings": ()},
    ):
        with pytest.raises(ValueError):
            DailyOperationsReport(**(data | change))
    finding = report.findings[0].model_dump()
    from app.services.operations_reports import DailyOperationsFinding

    for change in (
        {"severity": "warning"},
        {"canonical_status": "delivered"},
        {"recommended_action": "review_status_mapping"},
        {"source_status": "x"},
    ):
        with pytest.raises(ValueError):
            DailyOperationsFinding(**(finding | change))
    metrics = data["metrics"]
    with pytest.raises(ValueError):
        type(report.metrics)(**(metrics | {"orders_created": 5}))
    with pytest.raises(ValueError):
        type(report.metrics)(
            **(metrics | {"order_status_counts": metrics["order_status_counts"][1:]})
        )
