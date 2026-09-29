"""ShipmentQuery.store_id: the adapter enforces store scope through the PARENT order.

Contract: only shipments whose parent canonical order belongs to the store are
returned; an unknown store has no shipments (empty tuple, like an unknown order);
order_id + another store's order is empty; all filters combine with AND.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.commerce.domain import ShipmentStatus
from app.integrations.commerce import ShipmentQuery
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter
from app.integrations.commerce.mock.fixtures import ORDERS, SHIPMENTS
from tests.integrations.helpers import cid, run

NORTH, SOUTH = cid(EntityType.STORE, "shop_north"), cid(EntityType.STORE, "shop_south")
ORDER_SHOP = {o.order_key: o.shop_key for o in ORDERS}


def expected_keys(shop_key: str) -> set:
    return {cid(EntityType.SHIPMENT, s.shipment_key) for s in SHIPMENTS
            if ORDER_SHOP[s.order_key] == shop_key}  # fmt: skip


@pytest.mark.parametrize(("store", "shop_key"), [(NORTH, "shop_north"), (SOUTH, "shop_south")])
def test_store_scope_returns_exactly_that_stores_shipments(store, shop_key) -> None:
    adapter = MockCommerceAdapter()
    shipments = run(adapter.list_shipments(ShipmentQuery(store_id=store)))
    assert {s.id for s in shipments} == expected_keys(shop_key) != set()
    for shipment in shipments:  # every parent order is this store's
        assert run(adapter.get_order(shipment.order_id)).store_id == store
    everything = run(adapter.list_shipments())
    assert len(everything) == len(SHIPMENTS)
    assert len(everything) > len(shipments)


def test_both_stores_partition_all_shipments() -> None:
    adapter = MockCommerceAdapter()
    north = {s.id for s in run(adapter.list_shipments(ShipmentQuery(store_id=NORTH)))}
    south = {s.id for s in run(adapter.list_shipments(ShipmentQuery(store_id=SOUTH)))}
    assert north.isdisjoint(south)
    assert north | south == {s.id for s in run(adapter.list_shipments())}


def test_unknown_store_has_no_shipments() -> None:
    assert run(MockCommerceAdapter().list_shipments(ShipmentQuery(store_id=uuid4()))) == ()
    # A canonical id of another entity type is not a store either.
    order_id = cid(EntityType.ORDER, "ord_2002")
    assert run(MockCommerceAdapter().list_shipments(ShipmentQuery(store_id=order_id))) == ()


def test_order_of_another_store_never_leaks() -> None:
    adapter = MockCommerceAdapter()
    south_order = cid(EntityType.ORDER, "ord_2002")
    assert run(adapter.list_shipments(ShipmentQuery(order_id=south_order, store_id=NORTH))) == ()
    same = run(adapter.list_shipments(ShipmentQuery(order_id=south_order, store_id=SOUTH)))
    assert {s.id for s in same} == {cid(EntityType.SHIPMENT, "ship_507"),
                                    cid(EntityType.SHIPMENT, "ship_509")}  # fmt: skip


def test_filters_combine_with_and() -> None:
    adapter = MockCommerceAdapter()
    day = {
        "shipped_from": datetime(2026, 3, 2, 23, tzinfo=UTC),  # 2026-03-03 Berlin
        "shipped_to": datetime(2026, 3, 3, 23, tzinfo=UTC),
    }
    south_day = run(adapter.list_shipments(ShipmentQuery(store_id=SOUTH, **day)))
    assert [s.id for s in south_day] == [cid(EntityType.SHIPMENT, "ship_507")]
    assert south_day[0].status is ShipmentStatus.FAILED
    # The same window without store scope also contains a north shipment (ship_508).
    unscoped = {s.id for s in run(adapter.list_shipments(ShipmentQuery(**day)))}
    assert cid(EntityType.SHIPMENT, "ship_508") in unscoped
    assert run(adapter.list_shipments(ShipmentQuery(
        store_id=SOUTH, statuses=frozenset({ShipmentStatus.DELIVERED}), **day))) == ()  # fmt: skip
    limited = run(adapter.list_shipments(ShipmentQuery(store_id=SOUTH, limit=1)))
    assert len(limited) == 1 and limited[0].id in expected_keys("shop_south")
    delivered = run(adapter.list_shipments(ShipmentQuery(
        store_id=NORTH, statuses=frozenset({ShipmentStatus.DELIVERED}))))  # fmt: skip
    assert delivered and all(s.status is ShipmentStatus.DELIVERED for s in delivered)
    assert {s.id for s in delivered} <= expected_keys("shop_north")
