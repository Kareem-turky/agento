import inspect
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.integrations.commerce import (
    MAX_QUERY_LIMIT,
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
from app.integrations.commerce.mock import MockCommerceAdapter

T0 = datetime(2026, 3, 1, tzinfo=UTC)

CONTRACT_METHODS = (
    "get_store", "get_order", "list_orders", "get_shipment", "list_shipments", "get_inventory",
)  # fmt: skip


def test_capabilities_are_read_only() -> None:
    assert {c.value for c in IntegrationCapability} == {
        "orders_read", "shipments_read", "inventory_read",
    }  # fmt: skip


def test_descriptor_is_immutable_and_validated() -> None:
    descriptor = IntegrationDescriptor(id="x-1", name="X", capabilities=["orders_read"])
    assert descriptor.capabilities == frozenset({IntegrationCapability.ORDERS_READ})
    with pytest.raises(ValidationError):
        descriptor.name = "Y"
    for bad in ({"id": ""}, {"id": "Has Space"}, {"name": " "}, {"capabilities": ["orders_write"]},
                {"extra": 1}):  # fmt: skip
        with pytest.raises(ValidationError):
            IntegrationDescriptor(**{"id": "x", "name": "X", "capabilities": [], **bad})


def test_error_hierarchy() -> None:
    for error in (IntegrationNotFoundError, IntegrationUnavailableError, IntegrationDataError):
        assert issubclass(error, CommerceIntegrationError)
    order_id = uuid4()
    not_found = IntegrationNotFoundError("order", order_id)
    assert not_found.entity == "order" and not_found.entity_id == order_id
    assert str(order_id) in str(not_found)
    assert IntegrationUnavailableError("mock-commerce").integration_id == "mock-commerce"
    assert IntegrationDataError("order", "bad").reason == "bad"


def test_mock_adapter_conforms_to_the_protocol() -> None:
    # Static check: a type checker (pyright) verifies full signatures on this assignment.
    adapter: CommerceIntegration = MockCommerceAdapter()
    assert isinstance(adapter, CommerceIntegration)  # attribute presence only
    # Runtime Protocol checks do not compare signatures, so compare them explicitly.
    for name in CONTRACT_METHODS:
        expected = inspect.signature(getattr(CommerceIntegration, name))
        actual = inspect.signature(getattr(MockCommerceAdapter, name))
        assert list(actual.parameters) == list(expected.parameters), name
        assert [p.default for p in actual.parameters.values()] == [
            p.default for p in expected.parameters.values()
        ], name
        assert inspect.iscoroutinefunction(getattr(MockCommerceAdapter, name)), name
    assert isinstance(adapter.descriptor, IntegrationDescriptor)


def test_contract_signatures_take_no_actor_request_or_policy() -> None:
    for name in CONTRACT_METHODS:
        params = set(inspect.signature(getattr(CommerceIntegration, name)).parameters)
        assert params <= {"self", "store_id", "order_id", "shipment_id", "query",
                          "variant_id", "warehouse_id"}, name  # fmt: skip


def test_order_query_defaults_and_validation() -> None:
    query = OrderQuery()
    assert query.statuses == frozenset() and query.limit is None
    query = OrderQuery(statuses=["pending", "processing"], created_from=T0,
                       created_to=T0 + timedelta(days=1), limit=MAX_QUERY_LIMIT)  # fmt: skip
    assert query.statuses == {OrderStatus.PENDING, OrderStatus.PROCESSING}
    with pytest.raises(ValidationError):
        query.limit = 1


@pytest.mark.parametrize(
    "bad",
    [
        {"limit": 0},
        {"limit": MAX_QUERY_LIMIT + 1},
        {"limit": 5.0},
        {"limit": "5"},
        {"limit": True},
        {"statuses": ["late"]},
        {"statuses": ["in_transit"]},
        {"created_from": datetime(2026, 3, 1)},
        {"created_to": "2026-03-01T00:00:00"},
        {"created_from": T0, "created_to": T0},
        {"created_from": T0 + timedelta(hours=1), "created_to": T0},
        {"sort": "created_at"},
        {"where": "status == 'pending'"},
    ],
)
def test_order_query_rejects_invalid_input(bad: dict) -> None:
    with pytest.raises(ValidationError):
        OrderQuery(**bad)


def test_ranges_compare_instants_across_timezones() -> None:
    plus_two = timezone(timedelta(hours=2))
    with pytest.raises(ValidationError):  # 02:00+02:00 == 00:00Z
        OrderQuery(created_from=T0, created_to=datetime(2026, 3, 1, 2, tzinfo=plus_two))


def test_shipment_query_validation() -> None:
    query = ShipmentQuery(order_id=uuid4(), statuses=["delivered"], limit=1)
    assert query.statuses == {ShipmentStatus.DELIVERED}
    bad_inputs = (
        {"statuses": ["processing"]},
        {"shipped_from": datetime(2026, 3, 1)},
        {"shipped_from": T0, "shipped_to": T0},
        {"limit": 501},
        {"store_id": "not-a-uuid"},
        {"tenant_id": uuid4()},
    )
    for bad in bad_inputs:
        with pytest.raises(ValidationError):
            ShipmentQuery(**bad)
    # Task 019: store scope is part of the shipment query contract.
    store = uuid4()
    assert ShipmentQuery(store_id=store).store_id == store
