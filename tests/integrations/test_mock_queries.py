from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.integrations.commerce import IntegrationNotFoundError, OrderQuery, ShipmentQuery
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter
from app.integrations.commerce.mock.fixtures import default_dataset
from tests.integrations.helpers import cid, run

ADAPTER = MockCommerceAdapter()
NORTH = cid(EntityType.STORE, "shop_north")
SOUTH = cid(EntityType.STORE, "shop_south")


def orders(**query: object) -> list:
    return list(run(ADAPTER.list_orders(OrderQuery(**query))))


def shipments(**query: object) -> list:
    return list(run(ADAPTER.list_shipments(ShipmentQuery(**query))))


def keys(entities: list) -> list[str]:
    return [next(iter(e.external_refs)).external_id for e in entities]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


# ----- dataset ------------------------------------------------------------------------


def test_dataset_size_and_edge_cases() -> None:
    data = default_dataset()
    assert len(data.shops) == 2 and len(data.locations) == 2
    assert len(data.buyers) >= 5 and len(data.listings) >= 5 and len(data.skus) > len(data.listings)
    assert len(data.orders) >= 12 and len(data.shipments) >= 8

    statuses = {o.status for o in run(ADAPTER.list_orders())}
    assert {OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.FULFILLED,
            OrderStatus.CANCELLED, OrderStatus.UNKNOWN} <= statuses  # fmt: skip
    shipment_statuses = {s.status for s in run(ADAPTER.list_shipments())}
    assert {ShipmentStatus.PENDING, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED,
            ShipmentStatus.FAILED, ShipmentStatus.UNKNOWN} <= shipment_statuses  # fmt: skip
    available = {Decimal(r.sellable) for r in data.stock}
    assert Decimal(0) in available and any(a < 0 for a in available)
    assert any(0 < a <= 2 for a in available)
    assert any(o.buyer_key is None for o in data.orders)
    assert any(line.sku_key is None for o in data.orders for line in o.lines)


def test_dataset_timestamps_are_fixed_and_aware() -> None:
    first = run(ADAPTER.list_orders())
    assert first == run(MockCommerceAdapter().list_orders())
    for order in first:
        assert order.created_at.tzinfo is not None
        assert order.created_at.year == 2026


# ----- orders -------------------------------------------------------------------------


def test_all_orders_sorted_by_created_at_then_id() -> None:
    result = orders()
    assert len(result) == len(default_dataset().orders)
    assert result == sorted(result, key=lambda o: (o.created_at, o.id))
    assert keys(result)[:2] == ["ord_2004", "ord_1005"]


def test_orders_by_store() -> None:
    assert {o.store_id for o in orders(store_id=NORTH)} == {NORTH}
    assert len(orders(store_id=NORTH)) + len(orders(store_id=SOUTH)) == len(orders())
    assert orders(store_id=uuid4()) == []


def test_orders_by_status() -> None:
    assert keys(orders(statuses=["pending"])) == ["ord_1001", "ord_2001"]
    multi = orders(statuses=["processing", "unknown"])
    assert {o.status for o in multi} == {OrderStatus.PROCESSING, OrderStatus.UNKNOWN}
    assert len(multi) == 4
    assert orders(statuses=["completed"], store_id=SOUTH)[0].source_status == "closed"


def test_order_time_range_is_half_open() -> None:
    boundary = at("2026-03-03T08:15:00+00:00")  # ord_1003 created exactly here
    assert "ord_1003" in keys(orders(created_from=boundary))
    assert "ord_1003" not in keys(orders(created_to=boundary))
    # The same instant expressed in another timezone behaves identically.
    assert keys(orders(created_to=at("2026-03-03T10:15:00+02:00"))) == keys(
        orders(created_to=boundary)
    )
    window = orders(created_from=at("2026-03-02T00:00:00Z"), created_to=at("2026-03-03T00:00:00Z"))
    assert keys(window) == ["ord_1001", "ord_2001", "ord_1002"]


def test_combined_filters_and_limit() -> None:
    result = orders(store_id=NORTH, statuses=["processing", "pending"],
                    created_from=at("2026-03-02T00:00:00Z"), limit=2)  # fmt: skip
    assert keys(result) == ["ord_1001", "ord_1003"]
    assert len(orders(limit=1)) == 1
    assert orders(limit=500) == orders()


def test_empty_status_filter_means_all() -> None:
    assert orders(statuses=[]) == orders()
    assert run(ADAPTER.list_orders(None)) == run(ADAPTER.list_orders())


# ----- shipments ----------------------------------------------------------------------


def test_all_shipments_sorted_shipped_first_then_unshipped() -> None:
    result = shipments()
    assert len(result) == len(default_dataset().shipments)
    shipped = [s for s in result if s.shipped_at is not None]
    assert result[: len(shipped)] == sorted(shipped, key=lambda s: (s.shipped_at, s.id))
    assert all(s.shipped_at is None for s in result[len(shipped) :])
    assert keys(result)[0] == "ship_506"


def test_shipments_by_order() -> None:
    order_id = cid(EntityType.ORDER, "ord_2002")
    assert keys(shipments(order_id=order_id)) == ["ship_507", "ship_509"]
    assert shipments(order_id=uuid4()) == []


def test_shipments_by_status() -> None:
    assert keys(shipments(statuses=["in_transit"])) == ["ship_503", "ship_509"]
    assert {s.status for s in shipments(statuses=["failed", "unknown"])} == {
        ShipmentStatus.FAILED, ShipmentStatus.UNKNOWN,
    }  # fmt: skip


def test_shipment_time_range_is_half_open_and_skips_unshipped() -> None:
    boundary = at("2026-03-03T18:00:00Z")  # ship_508 shipped exactly here
    assert "ship_508" in keys(shipments(shipped_from=boundary))
    assert "ship_508" not in keys(shipments(shipped_to=boundary))
    ranged = shipments(shipped_from=at("2026-01-01T00:00:00Z"))
    assert all(s.shipped_at is not None for s in ranged)
    assert len(ranged) == len(shipments()) - 2


def test_shipment_limit() -> None:
    assert shipments(limit=3) == shipments()[:3]


# ----- inventory ----------------------------------------------------------------------


def test_inventory_for_one_warehouse() -> None:
    variant = cid(EntityType.VARIANT, "sku-tee-red-m")
    overflow = cid(EntityType.WAREHOUSE, "loc_overflow")
    levels = run(ADAPTER.get_inventory(variant, overflow))
    assert [level.warehouse_id for level in levels] == [overflow]
    assert levels[0].available == Decimal(10)


def test_inventory_edge_values() -> None:
    def available(sku: str) -> list[Decimal]:
        return [lvl.available for lvl in run(ADAPTER.get_inventory(cid(EntityType.VARIANT, sku)))]

    assert available("sku-tee-blue-m") == [Decimal(0)]
    assert available("sku-hoodie-grey-m") == [Decimal(-3)]
    assert available("sku-tee-red-l") == [Decimal(2)]
    mug = run(ADAPTER.get_inventory(cid(EntityType.VARIANT, "sku-mug-white")))[0]
    assert mug.on_hand is None and mug.reserved is None and mug.updated_at is None


def test_known_variant_without_stock_returns_empty() -> None:
    assert run(ADAPTER.get_inventory(cid(EntityType.VARIANT, "sku-cap-black"))) == ()
    main = cid(EntityType.WAREHOUSE, "loc_main")
    assert run(ADAPTER.get_inventory(cid(EntityType.VARIANT, "sku-hoodie-grey-m"), main)) == ()


def test_unknown_variant_or_warehouse_is_not_found() -> None:
    with pytest.raises(IntegrationNotFoundError) as exc:
        run(ADAPTER.get_inventory(uuid4()))
    assert exc.value.entity == "variant"
    with pytest.raises(IntegrationNotFoundError) as exc:
        run(ADAPTER.get_inventory(cid(EntityType.VARIANT, "sku-tee-red-m"), uuid4()))
    assert exc.value.entity == "warehouse"
    # A provider key is not a canonical ID: using the UUID of another entity type fails.
    with pytest.raises(IntegrationNotFoundError):
        run(ADAPTER.get_inventory(cid(EntityType.ORDER, "sku-tee-red-m")))


def test_created_at_filter_uses_aware_instants() -> None:
    assert orders(created_from=datetime(2026, 3, 6, tzinfo=UTC)) == orders(
        created_from=at("2026-03-06T01:00:00+01:00")
    )
