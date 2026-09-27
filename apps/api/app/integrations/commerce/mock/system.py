"""The mock external commerce system (the provider side).

In-memory and deterministic. It exposes provider-shaped reads that return provider
records, never canonical models, and raises its own provider-specific error when
"down". No network, no randomness, no clock.
"""

from app.integrations.commerce.mock.fixtures import default_dataset
from app.integrations.commerce.mock.models import (
    MockAccountRecord,
    MockBuyerRecord,
    MockDataset,
    MockListingRecord,
    MockLocationRecord,
    MockOrderRecord,
    MockShipmentRecord,
    MockShopRecord,
    MockSkuRecord,
    MockStockRecord,
)


class MockProviderDownError(Exception):
    """Provider-specific failure (stands in for a transport/SDK error)."""


class MockCommerceSystem:
    def __init__(self, dataset: MockDataset | None = None, *, available: bool = True) -> None:
        self._data = dataset if dataset is not None else default_dataset()
        self._available = available

    def with_availability(self, available: bool) -> "MockCommerceSystem":
        """The same data, reachable or not (deterministic outage simulation)."""
        return MockCommerceSystem(self._data, available=available)

    def _check(self) -> None:
        if not self._available:
            raise MockProviderDownError("mock commerce system is not responding")

    def fetch_account(self) -> MockAccountRecord:
        self._check()
        return self._data.account

    def fetch_shop(self, shop_key: str) -> MockShopRecord | None:
        self._check()
        return next((s for s in self._data.shops if s.shop_key == shop_key), None)

    def list_shop_keys(self) -> tuple[str, ...]:
        self._check()
        return tuple(s.shop_key for s in self._data.shops)

    def fetch_buyer(self, buyer_key: str) -> MockBuyerRecord | None:
        self._check()
        return next((b for b in self._data.buyers if b.buyer_key == buyer_key), None)

    def fetch_listing(self, listing_key: str) -> MockListingRecord | None:
        self._check()
        return next((p for p in self._data.listings if p.listing_key == listing_key), None)

    def fetch_sku(self, sku_key: str) -> MockSkuRecord | None:
        self._check()
        return next((s for s in self._data.skus if s.sku_key == sku_key), None)

    def list_sku_keys(self) -> tuple[str, ...]:
        self._check()
        return tuple(s.sku_key for s in self._data.skus)

    def fetch_location(self, location_key: str) -> MockLocationRecord | None:
        self._check()
        return next((w for w in self._data.locations if w.location_key == location_key), None)

    def list_location_keys(self) -> tuple[str, ...]:
        self._check()
        return tuple(w.location_key for w in self._data.locations)

    def fetch_order(self, order_key: str) -> MockOrderRecord | None:
        self._check()
        return next((o for o in self._data.orders if o.order_key == order_key), None)

    def search_orders(self, shop_key: str | None = None) -> tuple[MockOrderRecord, ...]:
        self._check()
        return tuple(o for o in self._data.orders if shop_key is None or o.shop_key == shop_key)

    def fetch_shipment(self, shipment_key: str) -> MockShipmentRecord | None:
        self._check()
        return next((s for s in self._data.shipments if s.shipment_key == shipment_key), None)

    def search_shipments(self, order_key: str | None = None) -> tuple[MockShipmentRecord, ...]:
        self._check()
        return tuple(
            s for s in self._data.shipments if order_key is None or s.order_key == order_key
        )

    def fetch_stock(
        self, sku_key: str, location_key: str | None = None
    ) -> tuple[MockStockRecord, ...]:
        self._check()
        return tuple(
            r
            for r in self._data.stock
            if r.sku_key == sku_key and (location_key is None or r.location_key == location_key)
        )
