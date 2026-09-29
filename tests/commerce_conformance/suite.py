"""The generic CommerceIntegration conformance checks (one place for every assertion).

Each check takes a ``CommerceConformanceFixture`` and raises ``AssertionError`` on any
contract violation. Query semantics are verified against an independent reference
computed from the adapter's own unfiltered results, so filtering, AND composition,
deterministic sorting and limit-after-sort are all checked together.

Contract pinned here (from ``app.integrations.commerce``):
- list results are tuples; orders sort by (created_at, id); shipments by
  (shipped_at ascending, unshipped last, id); inventory by warehouse_id;
- empty ``statuses`` = any status; time ranges are half-open ``from <= t < to``; a
  shipment time bound only matches shipments that have ``shipped_at``;
- ``limit`` applies after filtering and sorting;
- ``ShipmentQuery.store_id`` matches shipments whose PARENT order is that store's;
- an unknown store or order in a LIST query yields an empty tuple; an unknown entity
  in a GET raises ``IntegrationNotFoundError``;
- unreachable provider -> ``IntegrationUnavailableError``; unmappable data ->
  ``IntegrationDataError``; nothing else crosses the boundary.
"""

import asyncio
import inspect
import re
from collections.abc import Callable, Coroutine, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.commerce.domain import InventoryLevel, Order, Shipment, ShipmentStatus, Store
from app.integrations.commerce import (
    CommerceIntegration,
    CommerceIntegrationError,
    IntegrationCapability,
    IntegrationDataError,
    IntegrationDescriptor,
    IntegrationNotFoundError,
    IntegrationUnavailableError,
    OrderQuery,
    ShipmentQuery,
)
from tests.commerce_conformance.contract import CommerceConformanceFixture

CONTRACT_METHODS = ("get_store", "get_order", "list_orders", "get_shipment", "list_shipments",
                    "get_inventory")  # fmt: skip
READ_CAPABILITIES = frozenset(IntegrationCapability)
TICK = timedelta(microseconds=1)
_EARLIEST = datetime.min.replace(tzinfo=UTC)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _expect[E: CommerceIntegrationError](coro: Coroutine[Any, Any, object], error: type[E]) -> E:
    """The call must raise ``error`` (a Product integration error), nothing else."""
    try:
        result = _run(coro)
    except error as exc:
        assert isinstance(exc, CommerceIntegrationError)
        return exc
    except Exception as exc:  # noqa: BLE001 - reported as a contract violation
        raise AssertionError(f"expected {error.__name__}, got {type(exc).__name__}") from None
    raise AssertionError(f"expected {error.__name__}, got a result: {type(result).__name__}")


def _aware(value: datetime | None) -> bool:
    return value is None or value.utcoffset() is not None


def _order_key(order: Order) -> tuple[datetime, str]:
    return (order.created_at, str(order.id))


def _shipment_key(shipment: Shipment) -> tuple[bool, datetime, str]:
    return (shipment.shipped_at is None, shipment.shipped_at or _EARLIEST, str(shipment.id))


def _tuple_of[T](value: object, kind: type[T]) -> tuple[T, ...]:
    assert type(value) is tuple, f"list results must be a tuple, got {type(value).__name__}"
    assert all(isinstance(item, kind) for item in value), f"items must be {kind.__name__}"
    return value


def _in_range(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if start is None and end is None:
        return True
    if value is None:
        return False
    return (start is None or start <= value) and (end is None or value < end)


def _expected_orders(everything: tuple[Order, ...], query: OrderQuery) -> tuple[Order, ...]:
    matching = [
        o for o in everything
        if (query.store_id is None or o.store_id == query.store_id)
        and (not query.statuses or o.status in query.statuses)
        and _in_range(o.created_at, query.created_from, query.created_to)
    ]  # fmt: skip
    return tuple(sorted(matching, key=_order_key)[: query.limit])


def _expected_shipments(
    everything: tuple[Shipment, ...], store_of: dict[UUID, UUID], query: ShipmentQuery
) -> tuple[Shipment, ...]:
    matching = [
        s for s in everything
        if (query.order_id is None or s.order_id == query.order_id)
        and (query.store_id is None or store_of[s.order_id] == query.store_id)
        and (not query.statuses or s.status in query.statuses)
        and _in_range(s.shipped_at, query.shipped_from, query.shipped_to)
    ]  # fmt: skip
    return tuple(sorted(matching, key=_shipment_key)[: query.limit])


def _no_leaks(error: BaseException, markers: Iterable[str]) -> None:
    text = " ".join([str(error), repr(error), *map(str, error.args), repr(vars(error))])
    for marker in markers:
        assert marker not in text, "a provider-internal marker crossed the adapter boundary"


class _World:
    """The adapter's own unfiltered view, used as the reference for query checks."""

    def __init__(self, fixture: CommerceConformanceFixture) -> None:
        self.adapter = fixture.adapter()
        self.orders = _tuple_of(_run(self.adapter.list_orders()), Order)
        self.shipments = _tuple_of(_run(self.adapter.list_shipments()), Shipment)
        # Parent order -> store, established through the contract only.
        self.store_of = {
            order_id: _run(self.adapter.get_order(order_id)).store_id
            for order_id in {s.order_id for s in self.shipments}
        }

    def orders_for(self, query: OrderQuery) -> tuple[Order, ...]:
        return _tuple_of(_run(self.adapter.list_orders(query)), Order)

    def shipments_for(self, query: ShipmentQuery) -> tuple[Shipment, ...]:
        return _tuple_of(_run(self.adapter.list_shipments(query)), Shipment)


# ----- protocol and descriptor ------------------------------------------------------------


def check_protocol_and_signatures(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.adapter()
    assert isinstance(adapter, CommerceIntegration)
    for name in CONTRACT_METHODS:
        implementation = getattr(adapter, name, None)
        assert implementation is not None, f"missing {name}"
        assert inspect.iscoroutinefunction(implementation), f"{name} must be async"
        expected = list(inspect.signature(getattr(CommerceIntegration, name)).parameters.values())
        actual = list(inspect.signature(implementation).parameters.values())
        assert [(p.name, p.default) for p in actual] == [
            (p.name, p.default)
            for p in expected[1:]  # drop the protocol's ``self``
        ], f"{name} signature differs from the contract"


def check_descriptor_is_valid_and_read_only(fixture: CommerceConformanceFixture) -> None:
    descriptor = fixture.adapter().descriptor
    assert isinstance(descriptor, IntegrationDescriptor)
    assert IntegrationDescriptor.model_validate(descriptor.model_dump()) == descriptor
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", descriptor.id)
    assert descriptor.name.strip()
    assert isinstance(descriptor.capabilities, frozenset)
    # Truthful and read-only: every implemented read capability, no write capability.
    assert descriptor.capabilities == READ_CAPABILITIES
    assert all(c.value.endswith("_read") for c in descriptor.capabilities)
    try:
        descriptor.name = "changed"  # type: ignore[misc]
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("the descriptor must be immutable")


# ----- stores ----------------------------------------------------------------------------


def check_stores(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.adapter()
    for store_id in (fixture.store_a_id, fixture.store_b_id):
        store = _run(adapter.get_store(store_id))
        assert isinstance(store, Store) and store.id == store_id
        assert isinstance(store.company_id, UUID)
        assert store.name.strip() and store.timezone.strip()
        assert re.fullmatch(r"[A-Z]{3}", store.currency)
    assert fixture.store_a_id != fixture.store_b_id
    missing = uuid4()
    error = _expect(adapter.get_store(missing), IntegrationNotFoundError)
    assert error.entity_id == missing


# ----- orders ----------------------------------------------------------------------------


def _check_order(order: object, order_id: UUID) -> Order:
    assert isinstance(order, Order) and order.id == order_id
    assert isinstance(order.store_id, UUID) and order.store_id != order.id
    assert len(order.items) >= 1
    assert {item.unit_price.currency for item in order.items} == {order.total.currency}
    assert _aware(order.created_at) and _aware(order.updated_at)
    return order


def check_get_order(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.adapter()
    order_a = _check_order(_run(adapter.get_order(fixture.order_a_id)), fixture.order_a_id)
    order_b = _check_order(_run(adapter.get_order(fixture.order_b_id)), fixture.order_b_id)
    assert (order_a.store_id, order_b.store_id) == (fixture.store_a_id, fixture.store_b_id)
    missing = uuid4()
    error = _expect(adapter.get_order(missing), IntegrationNotFoundError)
    assert error.entity_id == missing


def check_list_orders_type_and_sorting(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    assert world.orders == tuple(sorted(world.orders, key=_order_key)), "orders are not sorted"
    assert len({o.id for o in world.orders}) == len(world.orders), "duplicate orders"
    assert {fixture.order_a_id, fixture.order_b_id} <= {o.id for o in world.orders}
    assert world.orders_for(OrderQuery()) == world.orders
    assert _tuple_of(_run(world.adapter.list_orders(None)), Order) == world.orders
    for order in world.orders:
        _check_order(order, order.id)


def check_order_store_scope(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    for store_id in (fixture.store_a_id, fixture.store_b_id):
        scoped = world.orders_for(OrderQuery(store_id=store_id))
        assert scoped and all(o.store_id == store_id for o in scoped), "another store leaked"
        assert scoped == _expected_orders(world.orders, OrderQuery(store_id=store_id))
    assert world.orders_for(OrderQuery(store_id=uuid4())) == ()  # unknown store: empty


def check_order_status_filter(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    statuses = sorted({o.status for o in world.orders})
    assert world.orders_for(OrderQuery(statuses=frozenset())) == world.orders
    for wanted in [frozenset({s}) for s in statuses] + [frozenset(statuses[:2])]:
        query = OrderQuery(statuses=wanted)
        result = world.orders_for(query)
        assert all(o.status in wanted for o in result)
        assert result == _expected_orders(world.orders, query)


def check_order_half_open_range(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    target = _run(world.adapter.get_order(fixture.order_a_id))
    at = target.created_at
    starting = world.orders_for(OrderQuery(created_from=at, created_to=at + TICK))
    assert target in starting, "a record exactly at the start must be included"
    ending = world.orders_for(OrderQuery(created_from=at - timedelta(days=1), created_to=at))
    assert target not in ending, "a record exactly at the end must be excluded"
    for query in (OrderQuery(created_from=at), OrderQuery(created_to=at),
                  OrderQuery(created_from=at, created_to=at + TICK)):  # fmt: skip
        assert world.orders_for(query) == _expected_orders(world.orders, query)


def check_order_composition_and_limit(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    target = _run(world.adapter.get_order(fixture.order_a_id))
    start, end = target.created_at - timedelta(days=30), target.created_at + timedelta(days=30)
    queries = [
        OrderQuery(limit=1),
        OrderQuery(limit=2),
        OrderQuery(store_id=fixture.store_a_id, limit=1),
        OrderQuery(store_id=fixture.store_a_id, statuses=frozenset({target.status})),
        OrderQuery(store_id=fixture.store_a_id, created_from=start, created_to=end),
        OrderQuery(store_id=fixture.store_a_id, statuses=frozenset({target.status}), limit=1,
                   created_from=start, created_to=end),
        OrderQuery(store_id=fixture.store_b_id, statuses=frozenset({target.status}),
                   created_from=start, created_to=end),
    ]  # fmt: skip
    for query in queries:
        assert world.orders_for(query) == _expected_orders(world.orders, query), query
    assert world.orders_for(OrderQuery(limit=1)) == world.orders[:1]


# ----- shipments ---------------------------------------------------------------------------


def _check_shipment(shipment: object, shipment_id: UUID) -> Shipment:
    assert isinstance(shipment, Shipment) and shipment.id == shipment_id
    assert isinstance(shipment.order_id, UUID) and shipment.order_id != shipment.id
    assert isinstance(shipment.status, ShipmentStatus)
    assert _aware(shipment.shipped_at) and _aware(shipment.delivered_at)
    return shipment


def check_get_shipment(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.adapter()
    for shipment_id, store_id in ((fixture.shipment_a_id, fixture.store_a_id),
                                  (fixture.shipment_b_id, fixture.store_b_id)):  # fmt: skip
        shipment = _check_shipment(_run(adapter.get_shipment(shipment_id)), shipment_id)
        assert _run(adapter.get_order(shipment.order_id)).store_id == store_id
    missing = uuid4()
    error = _expect(adapter.get_shipment(missing), IntegrationNotFoundError)
    assert error.entity_id == missing


def check_list_shipments_type_and_sorting(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    assert world.shipments == tuple(sorted(world.shipments, key=_shipment_key)), "not sorted"
    assert len({s.id for s in world.shipments}) == len(world.shipments), "duplicate shipments"
    assert {fixture.shipment_a_id, fixture.shipment_b_id} <= {s.id for s in world.shipments}
    unshipped = [s for s in world.shipments if s.shipped_at is None]
    assert unshipped, "the fixture must contain an unshipped shipment"
    assert world.shipments[-len(unshipped) :] == tuple(unshipped), "unshipped must sort last"
    assert world.shipments_for(ShipmentQuery()) == world.shipments
    assert _tuple_of(_run(world.adapter.list_shipments(None)), Shipment) == world.shipments
    for shipment in world.shipments:
        _check_shipment(shipment, shipment.id)


def check_shipment_order_scope(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    for order_id in sorted({s.order_id for s in world.shipments}, key=str):
        query = ShipmentQuery(order_id=order_id)
        result = world.shipments_for(query)
        assert result and all(s.order_id == order_id for s in result)
        assert result == _expected_shipments(world.shipments, world.store_of, query)
    assert world.shipments_for(ShipmentQuery(order_id=uuid4())) == ()  # unknown order


def check_shipment_store_scope(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    for store_id in (fixture.store_a_id, fixture.store_b_id):
        query = ShipmentQuery(store_id=store_id)
        result = world.shipments_for(query)
        assert result, "each fixture store has shipments"
        for shipment in result:  # the PARENT order proves the store, via the contract
            parent = _run(world.adapter.get_order(shipment.order_id))
            assert parent.store_id == store_id, "a shipment of another store leaked"
        assert result == _expected_shipments(world.shipments, world.store_of, query)
    assert world.shipments_for(ShipmentQuery(store_id=uuid4())) == ()  # unknown store


def check_shipment_order_and_store_cannot_cross(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    order_a = _run(world.adapter.get_shipment(fixture.shipment_a_id)).order_id
    order_b = _run(world.adapter.get_shipment(fixture.shipment_b_id)).order_id
    assert world.shipments_for(ShipmentQuery(order_id=order_b, store_id=fixture.store_a_id)) == ()
    assert world.shipments_for(ShipmentQuery(order_id=order_a, store_id=fixture.store_b_id)) == ()
    same = ShipmentQuery(order_id=order_a, store_id=fixture.store_a_id)
    result = world.shipments_for(same)
    assert result and result == _expected_shipments(world.shipments, world.store_of, same)


def check_shipment_status_filter(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    statuses = sorted({s.status for s in world.shipments})
    for wanted in [frozenset({s}) for s in statuses] + [frozenset(statuses[:2])]:
        query = ShipmentQuery(statuses=wanted)
        result = world.shipments_for(query)
        assert all(s.status in wanted for s in result)
        assert result == _expected_shipments(world.shipments, world.store_of, query)


def check_shipment_half_open_range_and_unshipped(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    target = _run(world.adapter.get_shipment(fixture.shipment_a_id))
    assert target.shipped_at is not None, "the fixture's store-A shipment must be shipped"
    at = target.shipped_at
    starting = world.shipments_for(ShipmentQuery(shipped_from=at, shipped_to=at + TICK))
    assert target in starting, "a shipment exactly at the start must be included"
    ending = world.shipments_for(ShipmentQuery(shipped_from=at - timedelta(days=1), shipped_to=at))
    assert target not in ending, "a shipment exactly at the end must be excluded"
    shipped = [s.shipped_at for s in world.shipments if s.shipped_at is not None]
    for query in (
        ShipmentQuery(shipped_from=min(shipped), shipped_to=max(shipped) + TICK),
        ShipmentQuery(shipped_from=min(shipped)),
        ShipmentQuery(shipped_to=max(shipped) + TICK),
    ):
        result = world.shipments_for(query)
        assert all(s.shipped_at is not None for s in result), "unshipped matched a time bound"
        assert result == _expected_shipments(world.shipments, world.store_of, query)


def check_shipment_composition_and_limit(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    target = _run(world.adapter.get_shipment(fixture.shipment_a_id))
    assert target.shipped_at is not None
    start, end = target.shipped_at - timedelta(days=30), target.shipped_at + timedelta(days=30)
    queries = [
        ShipmentQuery(limit=1),
        ShipmentQuery(limit=2),
        ShipmentQuery(store_id=fixture.store_a_id, limit=1),
        ShipmentQuery(store_id=fixture.store_a_id, statuses=frozenset({target.status})),
        ShipmentQuery(store_id=fixture.store_a_id, shipped_from=start, shipped_to=end),
        ShipmentQuery(order_id=target.order_id, store_id=fixture.store_a_id,
                      statuses=frozenset({target.status}), limit=1,
                      shipped_from=start, shipped_to=end),
        ShipmentQuery(store_id=fixture.store_b_id, statuses=frozenset({target.status}),
                      shipped_from=start, shipped_to=end),
    ]  # fmt: skip
    for query in queries:
        assert world.shipments_for(query) == _expected_shipments(
            world.shipments, world.store_of, query
        ), query
    assert world.shipments_for(ShipmentQuery(limit=1)) == world.shipments[:1]


# ----- inventory -----------------------------------------------------------------------------


def check_inventory(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.adapter()
    levels = _tuple_of(_run(adapter.get_inventory(fixture.variant_id)), InventoryLevel)
    assert levels, "the fixture variant has stock"
    assert list(levels) == sorted(levels, key=lambda level: level.warehouse_id)
    for level in levels:
        assert level.variant_id == fixture.variant_id
        assert isinstance(level.warehouse_id, UUID)
        assert InventoryLevel.model_validate(level.model_dump()) == level
    scoped = _tuple_of(_run(adapter.get_inventory(fixture.variant_id, fixture.warehouse_id)),
                       InventoryLevel)  # fmt: skip
    assert scoped and all(level.warehouse_id == fixture.warehouse_id for level in scoped)
    assert scoped == tuple(lv for lv in levels if lv.warehouse_id == fixture.warehouse_id)
    if fixture.variant_without_stock_id is not None:
        assert _run(adapter.get_inventory(fixture.variant_without_stock_id)) == ()
    missing = uuid4()
    assert _expect(adapter.get_inventory(missing), IntegrationNotFoundError).entity_id == missing
    unknown_warehouse = uuid4()
    error = _expect(adapter.get_inventory(fixture.variant_id, unknown_warehouse),
                    IntegrationNotFoundError)  # fmt: skip
    assert error.entity_id == unknown_warehouse


# ----- relationships, determinism, errors ------------------------------------------------------


def check_canonical_relationships(fixture: CommerceConformanceFixture) -> None:
    world = _World(fixture)
    for store_id in {o.store_id for o in world.orders}:
        assert _run(world.adapter.get_store(store_id)).id == store_id
    for order in world.orders:
        assert _run(world.adapter.get_order(order.id)) == order
    for shipment in world.shipments:
        assert _run(world.adapter.get_order(shipment.order_id)).id == shipment.order_id
        assert _run(world.adapter.get_shipment(shipment.id)) == shipment


def check_repeated_reads_are_deterministic(fixture: CommerceConformanceFixture) -> None:
    def snapshot(adapter: CommerceIntegration) -> tuple[object, ...]:
        return (
            _run(adapter.get_store(fixture.store_a_id)),
            _run(adapter.get_order(fixture.order_a_id)),
            _run(adapter.list_orders()),
            _run(adapter.list_orders(OrderQuery(store_id=fixture.store_b_id))),
            _run(adapter.get_shipment(fixture.shipment_a_id)),
            _run(adapter.list_shipments()),
            _run(adapter.list_shipments(ShipmentQuery(store_id=fixture.store_a_id))),
            _run(adapter.get_inventory(fixture.variant_id)),
        )

    adapter = fixture.adapter()
    first = snapshot(adapter)
    assert snapshot(adapter) == first, "repeated reads differ"
    assert snapshot(fixture.adapter()) == first, "a fresh adapter over the same state differs"


def check_unavailable_provider(fixture: CommerceConformanceFixture) -> None:
    adapter = fixture.unavailable_adapter()
    calls: list[Callable[[], Coroutine[Any, Any, object]]] = [
        lambda: adapter.get_store(fixture.store_a_id),
        lambda: adapter.get_order(fixture.order_a_id),
        lambda: adapter.list_orders(),
        lambda: adapter.list_orders(OrderQuery(store_id=fixture.store_a_id)),
        lambda: adapter.get_shipment(fixture.shipment_a_id),
        lambda: adapter.list_shipments(),
        lambda: adapter.list_shipments(ShipmentQuery(store_id=fixture.store_a_id)),
        lambda: adapter.get_inventory(fixture.variant_id),
    ]
    for call in calls:
        _no_leaks(_expect(call(), IntegrationUnavailableError), fixture.leak_markers)


def check_corrupted_provider_data(fixture: CommerceConformanceFixture) -> None:
    adapter, order_id = fixture.corrupted_order()
    _no_leaks(_expect(adapter.get_order(order_id), IntegrationDataError), fixture.leak_markers)
    # Never a partially valid list: the whole read fails.
    _no_leaks(_expect(adapter.list_orders(), IntegrationDataError), fixture.leak_markers)
    if fixture.corrupted_shipment is not None:
        adapter, shipment_id = fixture.corrupted_shipment()
        error = _expect(adapter.get_shipment(shipment_id), IntegrationDataError)
        _no_leaks(error, fixture.leak_markers)
        _no_leaks(_expect(adapter.list_shipments(), IntegrationDataError), fixture.leak_markers)
    if fixture.corrupted_store is not None:
        adapter, store_id = fixture.corrupted_store()
        _no_leaks(_expect(adapter.get_store(store_id), IntegrationDataError), fixture.leak_markers)


CONFORMANCE_CHECKS: tuple[Callable[[CommerceConformanceFixture], None], ...] = (
    check_protocol_and_signatures,
    check_descriptor_is_valid_and_read_only,
    check_stores,
    check_get_order,
    check_list_orders_type_and_sorting,
    check_order_store_scope,
    check_order_status_filter,
    check_order_half_open_range,
    check_order_composition_and_limit,
    check_get_shipment,
    check_list_shipments_type_and_sorting,
    check_shipment_order_scope,
    check_shipment_store_scope,
    check_shipment_order_and_store_cannot_cross,
    check_shipment_status_filter,
    check_shipment_half_open_range_and_unshipped,
    check_shipment_composition_and_limit,
    check_inventory,
    check_canonical_relationships,
    check_repeated_reads_are_deterministic,
    check_unavailable_provider,
    check_corrupted_provider_data,
)
