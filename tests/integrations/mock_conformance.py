"""MockCommerceAdapter's CommerceConformanceFixture (mock-specific glue only).

This is the ONLY conformance module that knows mock provider records and keys; it maps
them to canonical identities and controlled failure scenarios for the generic suite
in ``tests.commerce_conformance``.
"""

from dataclasses import replace
from uuid import UUID

from app.integrations.commerce import CommerceIntegration
from app.integrations.commerce.mock import (
    EntityType,
    MockCommerceAdapter,
    MockCommerceSystem,
    MockProviderDownError,
    canonical_id,
)
from app.integrations.commerce.mock.fixtures import ORDERS, SHIPMENTS, SHOPS, default_dataset
from tests.commerce_conformance import CommerceConformanceFixture

# Planted INSIDE the provider; none may appear in a Product-level error.
CREDENTIAL_MARKER = "CONFORMANCE-API-CREDENTIAL-5e1f"
URL_MARKER = "https://provider.invalid/v1/CONFORMANCE-URL-MARKER"
HEADER_MARKER = "Authorization: Bearer CONFORMANCE-HEADER-MARKER"
RAW_MARKER = '{"raw_payload": "CONFORMANCE-RAW-PAYLOAD-MARKER"}'
MARKERS = frozenset({CREDENTIAL_MARKER, URL_MARKER, HEADER_MARKER, RAW_MARKER,
                     "CONFORMANCE-URL-MARKER", "CONFORMANCE-HEADER-MARKER",
                     "CONFORMANCE-RAW-PAYLOAD-MARKER"})  # fmt: skip


class _DownSystem(MockCommerceSystem):
    """The provider is unreachable, and its transport error carries secrets."""

    def _check(self) -> None:
        raise MockProviderDownError(
            f"GET {URL_MARKER}?key={CREDENTIAL_MARKER} {HEADER_MARKER} -> 503 {RAW_MARKER}"
        )


def _adapter_with(**changes: object) -> CommerceIntegration:
    return MockCommerceAdapter(MockCommerceSystem(replace(default_dataset(), **changes)))


def _corrupted_order() -> tuple[CommerceIntegration, UUID]:
    orders = tuple(
        replace(o, gross_amount=f"not-a-number {RAW_MARKER} {CREDENTIAL_MARKER}")
        if o.order_key == "ord_2002" else o
        for o in ORDERS
    )  # fmt: skip
    return _adapter_with(orders=orders), canonical_id(EntityType.ORDER, "ord_2002")


def _corrupted_shipment() -> tuple[CommerceIntegration, UUID]:
    shipments = tuple(
        replace(s, dispatched_timestamp=RAW_MARKER) if s.shipment_key == "ship_507" else s
        for s in SHIPMENTS
    )
    return _adapter_with(shipments=shipments), canonical_id(EntityType.SHIPMENT, "ship_507")


def _corrupted_store() -> tuple[CommerceIntegration, UUID]:
    shops = tuple(
        replace(s, currency_code=RAW_MARKER) if s.shop_key == "shop_south" else s for s in SHOPS
    )
    return _adapter_with(shops=shops), canonical_id(EntityType.STORE, "shop_south")


def mock_conformance_fixture() -> CommerceConformanceFixture:
    return CommerceConformanceFixture(
        name="mock",
        adapter=MockCommerceAdapter,
        store_a_id=canonical_id(EntityType.STORE, "shop_south"),
        store_b_id=canonical_id(EntityType.STORE, "shop_north"),
        order_a_id=canonical_id(EntityType.ORDER, "ord_2002"),
        order_b_id=canonical_id(EntityType.ORDER, "ord_1003"),
        shipment_a_id=canonical_id(EntityType.SHIPMENT, "ship_507"),
        shipment_b_id=canonical_id(EntityType.SHIPMENT, "ship_502"),
        variant_id=canonical_id(EntityType.VARIANT, "sku-tee-red-m"),
        warehouse_id=canonical_id(EntityType.WAREHOUSE, "loc_main"),
        variant_without_stock_id=canonical_id(EntityType.VARIANT, "sku-cap-black"),
        unavailable_adapter=lambda: MockCommerceAdapter(_DownSystem()),
        corrupted_order=_corrupted_order,
        corrupted_shipment=_corrupted_shipment,
        corrupted_store=_corrupted_store,
        leak_markers=MARKERS,
    )
