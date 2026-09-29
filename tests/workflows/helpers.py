"""Test doubles for the daily operations workflow: real gate, spy/fake commerce."""

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.commerce.domain import (
    ExternalReference,
    Order,
    OrderStatus,
    Shipment,
    ShipmentStatus,
    Store,
)
from app.context.models import ActorContext, RequestContext
from app.governance import ActionCatalog, ActionScope, GovernanceGate
from app.integrations.commerce import IntegrationNotFoundError
from app.integrations.commerce.mock import EntityType, MockCommerceAdapter, canonical_id
from app.operations import OPERATIONS_ACTIONS
from app.workflows import DailyOperationsWorkflow

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))  # Europe/Berlin
NORTH = str(canonical_id(EntityType.STORE, "shop_north"))  # America/New_York
REPORT_PERMISSIONS = frozenset({"stores.read", "orders.read", "shipments.read"})
FIXED_NOW = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def actor(**overrides: Any) -> ActorContext:
    data: dict[str, Any] = {
        "actor_id": "report-reader", "actor_type": "user", "company_id": COMPANY,
        "permissions": REPORT_PERMISSIONS, "store_ids": frozenset({SOUTH, NORTH}),
    }  # fmt: skip
    return ActorContext(**(data | overrides))


def request(**overrides: Any) -> RequestContext:
    return RequestContext(actor=actor(**overrides))


def scope(store: str = SOUTH, company: str = COMPANY) -> ActionScope:
    return ActionScope(company_id=company, store_id=store)


def gate() -> GovernanceGate:
    return GovernanceGate(ActionCatalog(OPERATIONS_ACTIONS))


class SpyCommerce:
    """Delegates to an inner CommerceIntegration and records every call."""

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner if inner is not None else MockCommerceAdapter()
        self.calls: list[tuple[str, Any]] = []

    @property
    def descriptor(self):
        return self.inner.descriptor

    async def get_store(self, store_id):
        self.calls.append(("get_store", store_id))
        return await self.inner.get_store(store_id)

    async def get_order(self, order_id):
        self.calls.append(("get_order", order_id))
        return await self.inner.get_order(order_id)

    async def list_orders(self, query=None):
        self.calls.append(("list_orders", query))
        return await self.inner.list_orders(query)

    async def get_shipment(self, shipment_id):
        self.calls.append(("get_shipment", shipment_id))
        return await self.inner.get_shipment(shipment_id)

    async def list_shipments(self, query=None):
        self.calls.append(("list_shipments", query))
        return await self.inner.list_shipments(query)

    async def get_inventory(self, variant_id, warehouse_id=None):
        self.calls.append(("get_inventory", variant_id))
        return await self.inner.get_inventory(variant_id, warehouse_id)


def workflow(commerce: Any = None, clock=lambda: FIXED_NOW) -> DailyOperationsWorkflow:
    return DailyOperationsWorkflow(commerce=commerce or SpyCommerce(), gate=gate(), clock=clock)


# ----- canonical builders for fake integrations ---------------------------------------


def make_store(store_id: str = SOUTH, company: str = COMPANY, tz: str = "Europe/Berlin") -> Store:
    return Store(id=UUID(store_id), company_id=UUID(company), name="Store", currency="EUR",
                 timezone=tz)  # fmt: skip


def make_order(created_at: datetime, *, store: str = SOUTH, status=OrderStatus.PROCESSING,
               order_id: UUID | None = None) -> Order:  # fmt: skip
    return Order(
        id=order_id or uuid4(), store_id=UUID(store), status=status,
        source_status="provider-SECRET-order-status",
        items=({"title": "Item", "quantity": "1",
                "unit_price": {"amount": "1.00", "currency": "EUR"}},),
        total={"amount": "1.00", "currency": "EUR"}, created_at=created_at,
        external_refs=frozenset({
            ExternalReference(system="fake", external_id="PROVIDER-ORDER-KEY")}),
    )  # fmt: skip


def make_shipment(order: Order, shipped_at: datetime | None, *, status=ShipmentStatus.SHIPPED,
                  shipment_id: UUID | None = None) -> Shipment:  # fmt: skip
    return Shipment(
        id=shipment_id or uuid4(),
        order_id=order.id,
        status=status,
        source_status="provider-SECRET-shipment-status",
        courier_name="Courier",
        tracking_number="TRACK-SECRET-1",
        shipped_at=shipped_at,
    )


@dataclass
class FakeCommerce:
    """A controllable (possibly broken) CommerceIntegration. Ignores query filters on
    purpose, returning exactly what it was given, so the workflow's own validation is
    what is under test."""

    store: Any
    orders: Any = ()
    shipments: Any = ()
    extra_orders: dict = field(default_factory=dict)  # get_order-only records
    calls: list = field(default_factory=list)

    @property
    def descriptor(self):
        return MockCommerceAdapter().descriptor

    async def get_store(self, store_id):
        self.calls.append(("get_store", store_id))
        if isinstance(self.store, Exception):
            raise self.store
        return self.store

    async def list_orders(self, query=None):
        self.calls.append(("list_orders", query))
        return self.orders

    async def list_shipments(self, query=None):
        self.calls.append(("list_shipments", query))
        return self.shipments

    async def get_order(self, order_id):
        self.calls.append(("get_order", order_id))
        known = {o.id: o for o in self.orders if isinstance(o, Order)} | self.extra_orders
        if order_id not in known:
            raise IntegrationNotFoundError("order", order_id)
        return known[order_id]

    async def get_shipment(self, shipment_id):
        raise AssertionError("not used")

    async def get_inventory(self, variant_id, warehouse_id=None):
        raise AssertionError("inventory is not part of the report")
