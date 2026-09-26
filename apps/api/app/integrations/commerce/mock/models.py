"""Provider-side records of the mock commerce system.

These deliberately look like an EXTERNAL system's payloads: provider-style string
keys, provider field names, provider status vocabularies, amounts and timestamps as
strings. They are plain dataclasses, not canonical models, and are untrusted input
to the adapter. Nothing outside ``mock/`` may use them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MockAccountRecord:
    account_key: str
    display_name: str


@dataclass(frozen=True)
class MockShopRecord:
    shop_key: str
    account_key: str
    label: str
    currency_code: str
    tz_name: str


@dataclass(frozen=True)
class MockBuyerRecord:
    buyer_key: str
    shop_key: str
    full_name: str | None
    email_address: str | None
    phone_number: str | None


@dataclass(frozen=True)
class MockListingRecord:
    """A product listing with its purchasable SKUs."""

    listing_key: str
    shop_key: str
    headline: str
    listing_state: str


@dataclass(frozen=True)
class MockSkuRecord:
    sku_key: str  # e.g. "sku-tee-red-l"; also the provider's variant identifier
    listing_key: str
    option_label: str | None


@dataclass(frozen=True)
class MockLocationRecord:
    location_key: str
    account_key: str
    location_label: str


@dataclass(frozen=True)
class MockOrderLineRecord:
    line_key: str
    sku_key: str | None  # None for manual/custom lines
    description: str
    qty: str
    unit_amount: str


@dataclass(frozen=True)
class MockOrderRecord:
    order_key: str
    shop_key: str
    buyer_key: str | None
    state: str
    gross_amount: str
    currency: str
    created_timestamp: str
    modified_timestamp: str | None
    lines: tuple[MockOrderLineRecord, ...]


@dataclass(frozen=True)
class MockShipmentRecord:
    shipment_key: str
    order_key: str
    delivery_state: str
    carrier: str | None
    tracking_code: str | None
    dispatched_timestamp: str | None
    received_timestamp: str | None


@dataclass(frozen=True)
class MockStockRecord:
    sku_key: str
    location_key: str
    sellable: str
    physical: str | None
    held: str | None
    counted_timestamp: str | None


@dataclass(frozen=True)
class MockDataset:
    account: MockAccountRecord
    shops: tuple[MockShopRecord, ...]
    buyers: tuple[MockBuyerRecord, ...]
    listings: tuple[MockListingRecord, ...]
    skus: tuple[MockSkuRecord, ...]
    locations: tuple[MockLocationRecord, ...]
    orders: tuple[MockOrderRecord, ...]
    shipments: tuple[MockShipmentRecord, ...]
    stock: tuple[MockStockRecord, ...]
