"""Relationship integrity: provider references must exist and belong together."""

import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest

from app.integrations.commerce import IntegrationDataError, IntegrationNotFoundError
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter
from app.integrations.commerce.mock.fixtures import (
    LISTINGS,
    LOCATIONS,
    ORDERS,
    SHOPS,
    SKUS,
    STOCK,
)
from app.integrations.commerce.mock.models import MockSkuRecord
from tests.integrations.helpers import adapter_with, cid, run

ORDER_1001 = ORDERS[0]  # shop_north, buyer cus_001, line sku-tee-red-m
NORTH_LINE = ORDER_1001.lines[0]
FOREIGN = "acct_other"

# sha256 of the JSON of every order, shipment, inventory level and store mapped from the
# default dataset. Pinned so integrity checks can never silently change valid output.
DEFAULT_OUTPUT_DIGEST = "83903519361b12722edb7e8cc93cdf82b5517ad348c12754b8cc05aa39229645"


def only_order(**changes: object) -> MockCommerceAdapter:
    return adapter_with(orders=(replace(ORDER_1001, **changes),), shipments=())


def expect_data_error(adapter: MockCommerceAdapter) -> None:
    with pytest.raises(IntegrationDataError):
        run(adapter.list_orders())
    with pytest.raises(IntegrationDataError):
        run(adapter.get_order(cid(EntityType.ORDER, ORDER_1001.order_key)))


# ----- Order -> Store -----------------------------------------------------------------


def test_order_shop_must_exist() -> None:
    expect_data_error(only_order(shop_key="shop_missing"))


def test_shop_of_a_foreign_account_is_a_data_error() -> None:
    shops = (replace(SHOPS[0], account_key=FOREIGN), SHOPS[1])
    adapter = adapter_with(shops=shops, shipments=())
    with pytest.raises(IntegrationDataError):
        run(adapter.list_orders())
    with pytest.raises(IntegrationDataError):
        run(adapter.get_store(cid(EntityType.STORE, "shop_north")))


# ----- Order -> Customer --------------------------------------------------------------


def test_buyer_must_exist() -> None:
    expect_data_error(only_order(buyer_key="cus_missing"))


def test_buyer_from_another_store_is_a_data_error() -> None:
    expect_data_error(only_order(buyer_key="cus_004"))  # cus_004 belongs to shop_south


# ----- OrderItem -> SKU -> Listing -> Store -------------------------------------------


def test_line_sku_must_exist() -> None:
    expect_data_error(only_order(lines=(replace(NORTH_LINE, sku_key="sku-missing"),)))


def test_line_sku_from_another_store_is_a_data_error() -> None:
    expect_data_error(only_order(lines=(replace(NORTH_LINE, sku_key="sku-cap-black"),)))


def test_line_sku_with_missing_listing_is_a_data_error() -> None:
    orphan = MockSkuRecord("sku-orphan", "lst_missing", None)
    adapter = adapter_with(
        skus=(*SKUS, orphan),
        orders=(replace(ORDER_1001, lines=(replace(NORTH_LINE, sku_key="sku-orphan"),)),),
        shipments=(),
    )
    expect_data_error(adapter)


def test_manual_line_without_sku_stays_valid() -> None:
    manual = replace(NORTH_LINE, sku_key=None, description="Manual adjustment")
    order = run(only_order(lines=(manual,)).list_orders())[0]
    assert order.items[0].variant_id is None and order.items[0].sku is None

    default = MockCommerceAdapter()
    ord_1007 = run(default.get_order(cid(EntityType.ORDER, "ord_1007")))
    assert all(item.variant_id is None for item in ord_1007.items)
    mixed = run(default.get_order(cid(EntityType.ORDER, "ord_2002")))
    assert [item.variant_id is None for item in mixed.items] == [False, True]


# ----- Inventory -> Warehouse / Variant -----------------------------------------------


def inventory_of(adapter: MockCommerceAdapter, sku_key: str) -> object:
    return run(adapter.get_inventory(cid(EntityType.VARIANT, sku_key)))


def test_location_of_a_foreign_account_is_a_data_error() -> None:
    locations = (replace(LOCATIONS[0], account_key=FOREIGN), LOCATIONS[1])
    with pytest.raises(IntegrationDataError):
        inventory_of(adapter_with(locations=locations), "sku-tee-red-m")


def test_stock_location_must_exist() -> None:
    stock = (replace(STOCK[0], location_key="loc_missing"),)
    with pytest.raises(IntegrationDataError):
        inventory_of(adapter_with(stock=stock), STOCK[0].sku_key)


def test_stock_sku_with_missing_listing_is_a_data_error() -> None:
    orphan = MockSkuRecord("sku-orphan", "lst_missing", None)
    stock = (replace(STOCK[0], sku_key="sku-orphan"),)
    with pytest.raises(IntegrationDataError):
        inventory_of(adapter_with(skus=(*SKUS, orphan), stock=stock), "sku-orphan")


def test_stock_sku_whose_listing_shop_is_foreign_is_a_data_error() -> None:
    listings = tuple(
        replace(listing, shop_key="shop_elsewhere") if listing.listing_key == "lst_tee" else listing
        for listing in LISTINGS
    )
    with pytest.raises(IntegrationDataError):
        inventory_of(adapter_with(listings=listings), "sku-tee-red-m")


# ----- unchanged behaviour ------------------------------------------------------------


def test_valid_default_mappings_are_unchanged() -> None:
    adapter = MockCommerceAdapter()
    parts = [o.model_dump_json() for o in run(adapter.list_orders())]
    parts += [s.model_dump_json() for s in run(adapter.list_shipments())]
    for sku in SKUS:
        parts += [lvl.model_dump_json() for lvl in inventory_of(adapter, sku.sku_key)]
    for shop in SHOPS:
        parts.append(run(adapter.get_store(cid(EntityType.STORE, shop.shop_key))).model_dump_json())

    assert hashlib.sha256("\n".join(parts).encode()).hexdigest() == DEFAULT_OUTPUT_DIGEST


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.get_order(uuid4()),
        lambda a: a.get_shipment(uuid4()),
        lambda a: a.get_store(uuid4()),
        lambda a: a.get_inventory(uuid4()),
        lambda a: a.get_inventory(cid(EntityType.VARIANT, "sku-tee-red-m"), uuid4()),
    ],
)
def test_unknown_canonical_ids_are_still_not_found(call) -> None:
    # Even when some unrelated provider record is broken, lookup of an unknown canonical ID
    # reports not-found rather than a data error.
    broken = only_order(buyer_key="cus_004")
    for adapter in (MockCommerceAdapter(), broken):
        with pytest.raises(IntegrationNotFoundError):
            run(call(adapter))
