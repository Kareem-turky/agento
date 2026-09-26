from dataclasses import fields, is_dataclass
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.commerce.domain import (
    ExternalReference,
    InventoryLevel,
    Order,
    OrderStatus,
    Shipment,
    ShipmentStatus,
)
from app.integrations.commerce.mock import (
    MOCK_DESCRIPTOR,
    MOCK_ID_NAMESPACE,
    EntityType,
    MockCommerceAdapter,
    MockCommerceSystem,
    canonical_id,
)
from app.integrations.commerce.mock.fixtures import default_dataset
from app.integrations.commerce.mock.mapping import DELIVERY_STATE_MAP, ORDER_STATE_MAP
from app.integrations.commerce.mock.models import (
    MockOrderLineRecord,
    MockOrderRecord,
    MockShipmentRecord,
    MockStockRecord,
)
from tests.integrations.helpers import cid, run

ADAPTER = MockCommerceAdapter()
DATA = default_dataset()


def ref(key: str) -> frozenset[ExternalReference]:
    return frozenset({ExternalReference(system="mock-commerce", external_id=key)})


# ----- descriptor, identity ---------------------------------------------------------


def test_descriptor() -> None:
    assert ADAPTER.descriptor == MOCK_DESCRIPTOR
    assert MOCK_DESCRIPTOR.id == "mock-commerce"
    assert MOCK_DESCRIPTOR.name == "Mock Commerce System"
    assert {c.value for c in MOCK_DESCRIPTOR.capabilities} == {
        "orders_read", "shipments_read", "inventory_read",
    }  # fmt: skip


def test_canonical_ids_are_uuid5_stable_and_type_scoped() -> None:
    order_id = canonical_id(EntityType.ORDER, "ord_1001")
    assert isinstance(order_id, UUID) and order_id.version == 5
    assert order_id == canonical_id(EntityType.ORDER, "ord_1001")
    assert order_id != canonical_id(EntityType.SHIPMENT, "ord_1001")
    assert order_id != canonical_id(EntityType.ORDER, "ord_1002")
    # Pinned value: the mapping must never change silently between releases.
    assert MOCK_ID_NAMESPACE == UUID("5a0b7c3e-6f2d-4d8e-9b1a-3c4e5f607182")
    assert str(order_id) == str(canonical_id(EntityType.ORDER, "ord_1001"))


def test_repeated_reads_and_new_adapters_return_the_same_ids() -> None:
    first = run(ADAPTER.list_orders())
    again = run(MockCommerceAdapter(MockCommerceSystem()).list_orders())
    assert first == again
    assert [o.id for o in first] == [o.id for o in again]


def test_provider_ids_are_strings_not_uuids() -> None:
    keys = [o.order_key for o in DATA.orders] + [s.shipment_key for s in DATA.shipments]
    keys += [s.sku_key for s in DATA.skus]
    for key in keys:
        with pytest.raises(ValueError):
            UUID(key)


# ----- provider records are genuinely different ---------------------------------------


@pytest.mark.parametrize(
    ("record", "canonical"),
    [(MockOrderRecord, Order), (MockShipmentRecord, Shipment), (MockStockRecord, InventoryLevel)],
)
def test_provider_records_are_not_canonical_models(record: type, canonical: type) -> None:
    assert is_dataclass(record)
    assert not issubclass(record, BaseModel)
    assert record is not canonical
    assert not {f.name for f in fields(record)} & set(canonical.model_fields)


def test_order_line_record_differs_from_order_item() -> None:
    from app.commerce.domain import OrderItem

    assert not {f.name for f in fields(MockOrderLineRecord)} & set(OrderItem.model_fields)


# ----- orders -------------------------------------------------------------------------


def test_order_mapping_and_relationships() -> None:
    order = run(ADAPTER.get_order(cid(EntityType.ORDER, "ord_1003")))

    assert isinstance(order, Order)
    assert order.id == cid(EntityType.ORDER, "ord_1003")
    assert order.store_id == cid(EntityType.STORE, "shop_north")
    assert order.customer_id == cid(EntityType.CUSTOMER, "cus_001")
    assert order.status is OrderStatus.PROCESSING
    assert order.source_status == "packing"
    assert order.total.amount == Decimal("95.00") and order.total.currency == "USD"
    assert str(order.total.amount) == "95.00"  # exact, never through float
    assert order.created_at.isoformat() == "2026-03-03T08:15:00+00:00"
    assert order.external_refs == ref("ord_1003")
    assert [i.variant_id for i in order.items] == [
        cid(EntityType.VARIANT, "sku-hoodie-grey-l"), cid(EntityType.VARIANT, "sku-tee-blue-m"),
    ]  # fmt: skip
    assert order.items[1].quantity == Decimal("2")
    assert order.items[1].unit_price.amount == Decimal("20.00")
    assert order.items[1].id == cid(EntityType.ORDER_ITEM, "ord_1003-2")
    assert order.items[1].external_refs == ref("ord_1003-2")


def test_order_store_matches_mapped_store() -> None:
    order = run(ADAPTER.get_order(cid(EntityType.ORDER, "ord_2001")))
    store = run(ADAPTER.get_store(order.store_id))

    assert store.name == "South Storefront"
    assert store.currency == "EUR" == order.total.currency
    assert store.company_id == cid(EntityType.COMPANY, "acct_demo")
    assert store.external_refs == ref("shop_south")


def test_order_without_customer_and_manual_items() -> None:
    order = run(ADAPTER.get_order(cid(EntityType.ORDER, "ord_1007")))

    assert order.customer_id is None
    assert all(item.variant_id is None and item.sku is None for item in order.items)


@pytest.mark.parametrize(("provider", "canonical"), sorted(ORDER_STATE_MAP.items()))
def test_order_status_map(provider: str, canonical: OrderStatus) -> None:
    orders = [o for o in run(ADAPTER.list_orders()) if o.source_status == provider]
    assert orders, f"dataset has no {provider!r} order"
    assert {o.status for o in orders} == {canonical}


def test_unknown_order_status_is_preserved() -> None:
    order = run(ADAPTER.get_order(cid(EntityType.ORDER, "ord_1008")))
    assert order.status is OrderStatus.UNKNOWN
    assert order.source_status == "awaiting_fraud_review"


# ----- shipments ----------------------------------------------------------------------


def test_shipment_mapping() -> None:
    shipment = run(ADAPTER.get_shipment(cid(EntityType.SHIPMENT, "ship_504")))

    assert isinstance(shipment, Shipment)
    assert shipment.order_id == cid(EntityType.ORDER, "ord_1005")
    assert shipment.status is ShipmentStatus.DELIVERED
    assert shipment.source_status == "received"
    assert shipment.courier_name == "Sample Express"
    assert shipment.tracking_number == "SE000504"
    assert shipment.shipped_at is not None and shipment.delivered_at is not None
    assert shipment.external_refs == ref("ship_504")


def test_shipment_order_id_points_at_a_real_order() -> None:
    order_ids = {o.id for o in run(ADAPTER.list_orders())}
    assert {s.order_id for s in run(ADAPTER.list_shipments())} <= order_ids


@pytest.mark.parametrize(("provider", "canonical"), sorted(DELIVERY_STATE_MAP.items()))
def test_shipment_status_map(provider: str, canonical: ShipmentStatus) -> None:
    from tests.integrations.helpers import adapter_with

    record = MockShipmentRecord("ship_x", "ord_1001", provider, None, None, None, None)
    shipment = run(adapter_with(shipments=(record,)).list_shipments())[0]
    assert (shipment.status, shipment.source_status) == (canonical, provider)


def test_unknown_shipment_status_is_preserved() -> None:
    shipment = run(ADAPTER.get_shipment(cid(EntityType.SHIPMENT, "ship_508")))
    assert shipment.status is ShipmentStatus.UNKNOWN
    assert shipment.source_status == "held_at_customs"


def test_status_matching_is_exact() -> None:
    from tests.integrations.helpers import adapter_with

    record = MockShipmentRecord("ship_x", "ord_1001", "RECEIVED", None, None, None, None)
    shipment = run(adapter_with(shipments=(record,)).list_shipments())[0]
    assert shipment.status is ShipmentStatus.UNKNOWN and shipment.source_status == "RECEIVED"


# ----- inventory ----------------------------------------------------------------------


def test_inventory_mapping() -> None:
    variant_id = cid(EntityType.VARIANT, "sku-tee-red-m")
    levels = run(ADAPTER.get_inventory(variant_id))

    assert {level.warehouse_id for level in levels} == {
        cid(EntityType.WAREHOUSE, "loc_main"), cid(EntityType.WAREHOUSE, "loc_overflow"),
    }  # fmt: skip
    assert [level.warehouse_id for level in levels] == sorted(
        level.warehouse_id for level in levels
    )
    main = next(lvl for lvl in levels if lvl.warehouse_id == cid(EntityType.WAREHOUSE, "loc_main"))
    assert (main.available, main.on_hand, main.reserved) == (Decimal(42), Decimal(45), Decimal(3))
    assert main.variant_id == variant_id
    assert main.id == cid(EntityType.INVENTORY_LEVEL, "sku-tee-red-m@loc_main")
