"""Deterministic demo dataset for the mock commerce system.

Generic placeholder data only. Every timestamp is fixed and timezone-aware (never
relative to "now"), so tests and future SLA evaluations are reproducible. The data
deliberately includes operational edge cases (unknown provider statuses, failed
deliveries, low/zero/negative stock, an order without a buyer, a manual line without
a SKU); nothing here analyses them.
"""

from app.integrations.commerce.mock.models import (
    MockAccountRecord,
    MockBuyerRecord,
    MockDataset,
    MockListingRecord,
    MockLocationRecord,
    MockOrderLineRecord,
    MockOrderRecord,
    MockShipmentRecord,
    MockShopRecord,
    MockSkuRecord,
    MockStockRecord,
)

ACCOUNT = MockAccountRecord(account_key="acct_demo", display_name="Demo Commerce Co.")

SHOPS = (
    MockShopRecord("shop_north", "acct_demo", "North Storefront", "USD", "America/New_York"),
    MockShopRecord("shop_south", "acct_demo", "South Storefront", "EUR", "Europe/Berlin"),
)

BUYERS = (
    MockBuyerRecord("cus_001", "shop_north", "Alex Example", "alex@example.test", None),
    MockBuyerRecord("cus_002", "shop_north", "Sam Sample", None, "+10000000002"),
    MockBuyerRecord("cus_003", "shop_north", None, "guest3@example.test", None),
    MockBuyerRecord("cus_004", "shop_south", "Jo Placeholder", "jo@example.test", None),
    MockBuyerRecord("cus_005", "shop_south", "Robin Demo", None, None),
    MockBuyerRecord("cus_006", "shop_south", "Casey Test", "casey@example.test", "+10000000006"),
)

LISTINGS = (
    MockListingRecord("lst_tee", "shop_north", "Basic T-Shirt", "live"),
    MockListingRecord("lst_mug", "shop_north", "Ceramic Mug", "live"),
    MockListingRecord("lst_hoodie", "shop_north", "Zip Hoodie", "hidden"),
    MockListingRecord("lst_cap", "shop_south", "Baseball Cap", "live"),
    MockListingRecord("lst_bottle", "shop_south", "Steel Bottle", "live"),
)

SKUS = (
    MockSkuRecord("sku-tee-red-m", "lst_tee", "Red / M"),
    MockSkuRecord("sku-tee-red-l", "lst_tee", "Red / L"),
    MockSkuRecord("sku-tee-blue-m", "lst_tee", "Blue / M"),
    MockSkuRecord("sku-mug-white", "lst_mug", None),
    MockSkuRecord("sku-hoodie-grey-m", "lst_hoodie", "Grey / M"),
    MockSkuRecord("sku-hoodie-grey-l", "lst_hoodie", "Grey / L"),
    MockSkuRecord("sku-cap-black", "lst_cap", "Black"),
    MockSkuRecord("sku-bottle-steel", "lst_bottle", None),
)

LOCATIONS = (
    MockLocationRecord("loc_main", "acct_demo", "Main Warehouse"),
    MockLocationRecord("loc_overflow", "acct_demo", "Overflow Warehouse"),
)


def _line(key: str, sku: str | None, title: str, qty: str, price: str) -> MockOrderLineRecord:
    return MockOrderLineRecord(key, sku, title, qty, price)


ORDERS = (
    MockOrderRecord("ord_1001", "shop_north", "cus_001", "new", "40.00", "USD",
                    "2026-03-02T09:00:00Z", None,
                    (_line("ord_1001-1", "sku-tee-red-m", "Basic T-Shirt Red / M", "2", "20.00"),)),
    MockOrderRecord("ord_1002", "shop_north", "cus_002", "accepted", "12.50", "USD",
                    "2026-03-02T11:30:00Z", "2026-03-02T12:00:00Z",
                    (_line("ord_1002-1", "sku-mug-white", "Ceramic Mug", "1", "12.50"),)),
    MockOrderRecord("ord_1003", "shop_north", "cus_001", "packing", "95.00", "USD",
                    "2026-03-03T08:15:00Z", "2026-03-03T10:00:00Z",
                    (_line("ord_1003-1", "sku-hoodie-grey-l", "Hoodie Grey / L", "1", "55.00"),
                     _line("ord_1003-2", "sku-tee-blue-m", "T-Shirt Blue / M", "2", "20.00"))),
    MockOrderRecord("ord_1004", "shop_north", "cus_003", "handed_over", "20.00", "USD",
                    "2026-03-03T14:45:00-05:00", "2026-03-04T09:00:00-05:00",
                    (_line("ord_1004-1", "sku-tee-red-l", "Basic T-Shirt Red / L", "1", "20.00"),)),
    MockOrderRecord("ord_1005", "shop_north", "cus_002", "closed", "25.00", "USD",
                    "2026-03-01T10:00:00Z", "2026-03-05T16:00:00Z",
                    (_line("ord_1005-1", "sku-mug-white", "Ceramic Mug", "2", "12.50"),)),
    MockOrderRecord("ord_1006", "shop_north", "cus_001", "voided", "20.00", "USD",
                    "2026-03-04T07:20:00Z", "2026-03-04T07:45:00Z",
                    (_line("ord_1006-1", "sku-tee-red-m", "Basic T-Shirt Red / M", "1", "20.00"),)),
    MockOrderRecord("ord_1007", "shop_north", None, "packing", "15.00", "USD",
                    "2026-03-05T13:00:00Z", None,
                    (_line("ord_1007-1", None, "Gift wrapping (manual)", "1", "5.00"),
                     _line("ord_1007-2", None, "Custom engraving", "1", "10.00"))),
    MockOrderRecord("ord_1008", "shop_north", "cus_003", "awaiting_fraud_review", "55.00", "USD",
                    "2026-03-05T18:10:00Z", None,
                    (_line("ord_1008-1", "sku-hoodie-grey-m", "Hoodie Grey / M", "1", "55.00"),)),
    MockOrderRecord("ord_2001", "shop_south", "cus_004", "new", "18.00", "EUR",
                    "2026-03-02T10:00:00+01:00", None,
                    (_line("ord_2001-1", "sku-cap-black", "Baseball Cap Black", "1", "18.00"),)),
    MockOrderRecord("ord_2002", "shop_south", "cus_005", "packing", "54.00", "EUR",
                    "2026-03-03T09:30:00+01:00", "2026-03-03T11:00:00+01:00",
                    (_line("ord_2002-1", "sku-bottle-steel", "Steel Bottle", "2", "24.00"),
                     _line("ord_2002-2", None, "Express handling fee", "1", "6.00"))),
    MockOrderRecord("ord_2003", "shop_south", "cus_006", "handed_over", "24.00", "EUR",
                    "2026-03-04T15:00:00+01:00", "2026-03-05T08:00:00+01:00",
                    (_line("ord_2003-1", "sku-bottle-steel", "Steel Bottle", "1", "24.00"),)),
    MockOrderRecord("ord_2004", "shop_south", "cus_004", "closed", "36.00", "EUR",
                    "2026-02-27T12:00:00+01:00", "2026-03-03T12:00:00+01:00",
                    (_line("ord_2004-1", "sku-cap-black", "Baseball Cap Black", "2", "18.00"),)),
    MockOrderRecord("ord_2005", "shop_south", "cus_005", "open_draft", "18.00", "EUR",
                    "2026-03-06T09:00:00+01:00", None,
                    (_line("ord_2005-1", "sku-cap-black", "Baseball Cap Black", "1", "18.00"),)),
)  # fmt: skip

SHIPMENTS = (
    MockShipmentRecord("ship_501", "ord_1002", "label_created", "Demo Courier", None, None, None),
    MockShipmentRecord("ship_502", "ord_1003", "waiting_pickup", "Demo Courier", "DC000502",
                       None, None),
    MockShipmentRecord("ship_503", "ord_1004", "moving", "Demo Courier", "DC000503",
                       "2026-03-04T10:00:00-05:00", None),
    MockShipmentRecord("ship_504", "ord_1005", "received", "Sample Express", "SE000504",
                       "2026-03-02T09:00:00Z", "2026-03-04T15:30:00Z"),
    MockShipmentRecord("ship_505", "ord_2003", "picked_up", "Sample Express", "SE000505",
                       "2026-03-05T09:00:00+01:00", None),
    MockShipmentRecord("ship_506", "ord_2004", "received", "Demo Courier", "DC000506",
                       "2026-02-28T08:00:00+01:00", "2026-03-02T13:00:00+01:00"),
    MockShipmentRecord("ship_507", "ord_2002", "delivery_failed", "Sample Express", "SE000507",
                       "2026-03-03T16:00:00+01:00", None),
    MockShipmentRecord("ship_508", "ord_1003", "held_at_customs", "Demo Courier", "DC000508",
                       "2026-03-03T18:00:00Z", None),
    MockShipmentRecord("ship_509", "ord_2002", "moving", "Demo Courier", "DC000509",
                       "2026-03-05T07:30:00+01:00", None),
)  # fmt: skip

STOCK = (
    MockStockRecord("sku-tee-red-m", "loc_main", "42", "45", "3", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-tee-red-m", "loc_overflow", "10", "10", "0", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-tee-red-l", "loc_main", "2", "3", "1", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-tee-blue-m", "loc_main", "0", "0", "0", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-mug-white", "loc_main", "120", None, None, None),
    MockStockRecord("sku-hoodie-grey-m", "loc_overflow", "-3", "0", "3", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-hoodie-grey-l", "loc_main", "7", "8", "1", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-bottle-steel", "loc_main", "1", "4", "3", "2026-03-06T06:00:00Z"),
    MockStockRecord("sku-bottle-steel", "loc_overflow", "15", "15", "0", "2026-03-06T06:00:00Z"),
    # "sku-cap-black" deliberately has no stock records.
)  # fmt: skip


def default_dataset() -> MockDataset:
    return MockDataset(
        account=ACCOUNT,
        shops=SHOPS,
        buyers=BUYERS,
        listings=LISTINGS,
        skus=SKUS,
        locations=LOCATIONS,
        orders=ORDERS,
        shipments=SHIPMENTS,
        stock=STOCK,
    )
