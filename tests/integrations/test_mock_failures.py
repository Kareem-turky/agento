from dataclasses import replace
from uuid import uuid4

import pytest

from app.integrations.commerce import (
    IntegrationDataError,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
)
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter, MockCommerceSystem
from app.integrations.commerce.mock.fixtures import ORDERS, SHIPMENTS, STOCK
from app.integrations.commerce.mock.models import MockOrderRecord, MockShipmentRecord
from app.integrations.commerce.mock.system import MockProviderDownError
from tests.integrations.helpers import adapter_with, cid, run

ADAPTER = MockCommerceAdapter()
ORDER_1001 = ORDERS[0]


# ----- not found ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "entity"),
    [("get_order", "order"), ("get_shipment", "shipment"), ("get_store", "store")],
)
def test_unknown_ids_raise_not_found(method: str, entity: str) -> None:
    unknown = uuid4()
    with pytest.raises(IntegrationNotFoundError) as exc:
        run(getattr(ADAPTER, method)(unknown))
    assert exc.value.entity == entity and exc.value.entity_id == unknown
    assert "ord_" not in str(exc.value) and "ship_" not in str(exc.value)


def test_uuid_of_another_entity_type_is_not_found() -> None:
    with pytest.raises(IntegrationNotFoundError):
        run(ADAPTER.get_order(cid(EntityType.SHIPMENT, "ord_1001")))
    with pytest.raises(IntegrationNotFoundError):
        run(ADAPTER.get_shipment(cid(EntityType.ORDER, "ship_501")))


# ----- unavailability -----------------------------------------------------------------


def test_provider_error_exists_only_on_the_provider_side() -> None:
    down = MockCommerceSystem(available=False)
    with pytest.raises(MockProviderDownError):
        down.search_orders()


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.get_order(cid(EntityType.ORDER, "ord_1001")),
        lambda a: a.list_orders(),
        lambda a: a.get_shipment(cid(EntityType.SHIPMENT, "ship_501")),
        lambda a: a.list_shipments(),
        lambda a: a.get_inventory(cid(EntityType.VARIANT, "sku-tee-red-m")),
        lambda a: a.get_store(cid(EntityType.STORE, "shop_north")),
    ],
)
def test_unavailable_provider_becomes_integration_unavailable(call) -> None:
    adapter = MockCommerceAdapter(MockCommerceSystem().with_availability(False))
    with pytest.raises(IntegrationUnavailableError) as exc:
        run(call(adapter))
    assert exc.value.integration_id == "mock-commerce"
    assert isinstance(exc.value.__cause__, MockProviderDownError)


def test_availability_is_deterministic_and_recoverable() -> None:
    system = MockCommerceSystem()
    down = system.with_availability(False)
    assert run(MockCommerceAdapter(down.with_availability(True)).list_orders()) == run(
        MockCommerceAdapter(system).list_orders()
    )


# ----- malformed provider data --------------------------------------------------------


def bad_order(**changes: object) -> MockCommerceAdapter:
    return adapter_with(orders=(replace(ORDER_1001, **changes),), shipments=())


BAD_LINE = ORDER_1001.lines[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"gross_amount": "12.3.4"},
        {"gross_amount": "NaN"},
        {"gross_amount": ""},
        {"gross_amount": 40.0},  # a float must never become money
        {"currency": "US"},
        {"created_timestamp": "2026-03-02T09:00:00"},  # naive
        {"created_timestamp": "yesterday"},
        {"modified_timestamp": "2026-03-02 09:00"},
        {"lines": ()},
        {"lines": (replace(BAD_LINE, qty="0"),)},
        {"lines": (replace(BAD_LINE, qty="two"),)},
        {"lines": (replace(BAD_LINE, unit_amount="abc"),)},
        {"lines": (replace(BAD_LINE, description="  "),)},
        {"lines": (replace(BAD_LINE, sku_key="sku-does-not-exist"),)},
        {"shop_key": "shop_missing"},
        {"buyer_key": "cus_missing"},
        {"state": 5},
        {"gross_amount": 40},  # numbers are not provider amounts
        {"created_timestamp": 1772442000},  # not reinterpreted as an epoch
    ],
)
def test_malformed_order_becomes_data_error(changes: dict) -> None:
    adapter = bad_order(**changes)
    with pytest.raises(IntegrationDataError) as exc:
        run(adapter.list_orders())
    assert exc.value.entity in {"order", "order item"}
    with pytest.raises(IntegrationDataError):
        run(adapter.get_order(cid(EntityType.ORDER, "ord_1001")))


def test_data_error_chains_the_original_and_hides_provider_ids() -> None:
    with pytest.raises(IntegrationDataError) as exc:
        run(bad_order(gross_amount="12.3.4").list_orders())
    assert exc.value.__cause__ is not None
    assert "ord_1001" not in str(exc.value) and "12.3.4" not in str(exc.value)


@pytest.mark.parametrize(
    "record",
    [
        MockShipmentRecord("ship_x", "ord_missing", "moving", None, None, None, None),
        MockShipmentRecord("ship_x", "ord_1001", "moving", None, None, "2026-03-01T10:00:00", None),
        MockShipmentRecord("ship_x", "ord_1001", "moving", "  ", None, None, 5),
    ],
)
def test_malformed_shipment_becomes_data_error(record: MockShipmentRecord) -> None:
    with pytest.raises(IntegrationDataError):
        run(adapter_with(shipments=(record,)).list_shipments())


@pytest.mark.parametrize(
    "changes",
    [
        {"sellable": "lots"},
        {"sellable": 3.5},
        {"sellable": 3},
        {"physical": "Infinity"},
        {"counted_timestamp": 0},
        {"counted_timestamp": "2026-03-06T06:00:00"},
        {"location_key": "loc_missing"},
    ],
)
def test_malformed_stock_becomes_data_error(changes: dict) -> None:
    adapter = adapter_with(stock=(replace(STOCK[0], **changes),))
    with pytest.raises(IntegrationDataError):
        run(adapter.get_inventory(cid(EntityType.VARIANT, STOCK[0].sku_key)))


def test_unknown_status_is_not_a_data_error() -> None:
    record: MockOrderRecord = replace(ORDER_1001, state="some_new_provider_state")
    order = run(adapter_with(orders=(record,), shipments=()).list_orders())[0]
    assert order.source_status == "some_new_provider_state"


def test_default_fixtures_are_all_mappable() -> None:
    assert len(run(ADAPTER.list_orders())) == len(ORDERS)
    assert len(run(ADAPTER.list_shipments())) == len(SHIPMENTS)
