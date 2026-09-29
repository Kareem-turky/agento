"""The conformance harness is meaningful: deliberately broken adapters are caught.

Each broken adapter wraps a conforming one and breaks exactly one rule; the focused
harness check for that rule must fail, while the unbroken fixture passes it.
Test-only doubles: none of this exists in application code.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.integrations.commerce import IntegrationUnavailableError, OrderQuery, ShipmentQuery
from app.integrations.commerce.mock import MockCommerceAdapter
from tests.commerce_conformance import suite
from tests.integrations.mock_conformance import (
    CREDENTIAL_MARKER,
    mock_conformance_fixture,
)

TICK = timedelta(microseconds=1)


class Delegating:
    """A conforming adapter, passed through unchanged unless a subclass overrides."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @property
    def descriptor(self):
        return self.inner.descriptor

    async def get_store(self, store_id):
        return await self.inner.get_store(store_id)

    async def get_order(self, order_id):
        return await self.inner.get_order(order_id)

    async def list_orders(self, query=None):
        return await self.inner.list_orders(query)

    async def get_shipment(self, shipment_id):
        return await self.inner.get_shipment(shipment_id)

    async def list_shipments(self, query=None):
        return await self.inner.list_shipments(query)

    async def get_inventory(self, variant_id, warehouse_id=None):
        return await self.inner.get_inventory(variant_id, warehouse_id)


class WrongOrderSorting(Delegating):
    async def list_orders(self, query=None):
        return tuple(reversed(await self.inner.list_orders(query)))


class CrossStoreShipmentLeak(Delegating):
    async def list_shipments(self, query=None):
        if query is not None and query.store_id is not None:
            query = query.model_copy(update={"store_id": None})  # store filter ignored
        return await self.inner.list_shipments(query)


class InclusiveEndTime(Delegating):
    async def list_orders(self, query=None):
        if query is not None and query.created_to is not None:
            query = OrderQuery(**(query.model_dump() | {"created_to": query.created_to + TICK}))
        return await self.inner.list_orders(query)

    async def list_shipments(self, query=None):
        if query is not None and query.shipped_to is not None:
            query = ShipmentQuery(**(query.model_dump() | {"shipped_to": query.shipped_to + TICK}))
        return await self.inner.list_shipments(query)


class ListInsteadOfTuple(Delegating):
    async def list_orders(self, query=None):
        return list(await self.inner.list_orders(query))


class RawProviderException(Delegating):
    """An unreachable provider whose transport exception escapes untranslated."""

    async def get_store(self, store_id):
        raise ConnectionError("connection refused")

    async def get_order(self, order_id):
        raise ConnectionError("connection refused")

    async def list_orders(self, query=None):
        raise ConnectionError("connection refused")


class LeakingUnavailable(Delegating):
    """Translated, but the Product-level message carries a provider credential."""

    async def get_store(self, store_id):
        error = IntegrationUnavailableError("provider")
        error.args = (f"unavailable (key={CREDENTIAL_MARKER})",)
        raise error


class NoneForUnknownStore(Delegating):
    async def get_store(self, store_id):
        try:
            return await self.inner.get_store(store_id)
        except Exception:  # noqa: BLE001 - the bug under test: swallow "not found"
            return None


class SomethingOnUnknownStoreList(Delegating):
    async def list_orders(self, query=None):
        if query is not None and query.store_id is not None:
            result = await self.inner.list_orders(query)
            return result or await self.inner.list_orders(None)  # unknown store: everything
        return await self.inner.list_orders(query)


def broken(adapter_type: type[Delegating], *, unavailable: bool = False):
    base = mock_conformance_fixture()
    wrapped = lambda: adapter_type(MockCommerceAdapter())  # noqa: E731
    if unavailable:
        return replace(base, unavailable_adapter=wrapped)
    return replace(base, adapter=wrapped)


@pytest.mark.parametrize(
    ("adapter_type", "check", "unavailable"),
    [
        (WrongOrderSorting, suite.check_list_orders_type_and_sorting, False),
        (CrossStoreShipmentLeak, suite.check_shipment_store_scope, False),
        (CrossStoreShipmentLeak, suite.check_shipment_order_and_store_cannot_cross, False),
        (InclusiveEndTime, suite.check_order_half_open_range, False),
        (InclusiveEndTime, suite.check_shipment_half_open_range_and_unshipped, False),
        (ListInsteadOfTuple, suite.check_list_orders_type_and_sorting, False),
        (RawProviderException, suite.check_unavailable_provider, True),
        (LeakingUnavailable, suite.check_unavailable_provider, True),
        (NoneForUnknownStore, suite.check_stores, False),
        (SomethingOnUnknownStoreList, suite.check_order_store_scope, False),
    ],
    ids=lambda v: getattr(v, "__name__", str(v)),
)
def test_the_harness_catches_the_broken_rule(adapter_type, check, unavailable) -> None:
    check(mock_conformance_fixture())  # the conforming adapter passes this check ...
    with pytest.raises(AssertionError):  # ... and the broken one is caught by it
        check(broken(adapter_type, unavailable=unavailable))


def test_a_wrapper_that_breaks_nothing_passes_the_whole_harness() -> None:
    fixture = broken(Delegating)
    for check in suite.CONFORMANCE_CHECKS:
        check(fixture)
