"""JSON round trips keep UUIDs, exact decimals, enums, references and aware datetimes."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.commerce.domain import (
    Company,
    Customer,
    ExternalReference,
    InventoryLevel,
    Order,
    OrderStatus,
    Product,
    ProductStatus,
    Shipment,
    ShipmentStatus,
    Store,
    Variant,
    Warehouse,
)
from tests.commerce.factories import item, money, order

REFS = frozenset(
    {
        ExternalReference(system="source-a", external_id="A-1"),
        ExternalReference(system="source-b", external_id="B-9"),
    }
)
PLUS_TWO = timezone(timedelta(hours=2))


def sample_order() -> Order:
    return order(
        customer_id=uuid4(),
        status=OrderStatus.FULFILLED,
        source_status="shipped_partial",
        items=(
            item(
                quantity=Decimal("1.5"), unit_price=money("0.1000000000000000055511151231", "JOD")
            ),
            item(quantity=Decimal("3"), unit_price=money("-2.005", "jod"), external_refs=REFS),
        ),
        total=money("12345678901234567890.123", "JOD"),
        created_at=datetime(2026, 1, 15, 10, 30, 15, 123456, tzinfo=PLUS_TWO),
        updated_at=datetime(2026, 1, 16, tzinfo=UTC),
        external_refs=REFS,
    )


SAMPLES = [
    Company(name="Test Company", external_refs=REFS),
    Store(company_id=uuid4(), name="S", currency="usd", timezone="UTC", external_refs=REFS),
    Customer(store_id=uuid4(), name="N", email="e@example.invalid"),
    Product(store_id=uuid4(), title="P", status=ProductStatus.DRAFT, source_status="hidden"),
    Variant(product_id=uuid4(), sku="SKU"),
    Warehouse(company_id=uuid4(), name="W"),
    InventoryLevel(
        variant_id=uuid4(),
        warehouse_id=uuid4(),
        available=Decimal("-1.25"),
        on_hand=Decimal("0"),
        updated_at=datetime(2026, 2, 1, 8, 0, tzinfo=PLUS_TWO),
    ),
    sample_order(),
    Shipment(
        order_id=uuid4(),
        status=ShipmentStatus.DELIVERED,
        source_status="DLV",
        shipped_at=datetime(2026, 1, 16, tzinfo=UTC),
        delivered_at=datetime(2026, 1, 18, 14, 5, tzinfo=PLUS_TWO),
        external_refs=REFS,
    ),
]


@pytest.mark.parametrize("entity", SAMPLES, ids=lambda e: type(e).__name__)
def test_json_mode_round_trip_is_lossless(entity) -> None:
    dumped = entity.model_dump(mode="json")
    restored = type(entity).model_validate(dumped)

    assert restored == entity
    assert restored.model_dump(mode="json") == dumped


@pytest.mark.parametrize("entity", SAMPLES, ids=lambda e: type(e).__name__)
def test_json_string_round_trip_is_lossless(entity) -> None:
    assert type(entity).model_validate_json(entity.model_dump_json()) == entity


def test_order_round_trip_details() -> None:
    original = sample_order()
    dumped = original.model_dump(mode="json")
    restored = Order.model_validate(dumped)

    # Exact decimals travel as strings, never as floats.
    assert dumped["total"] == {"amount": "12345678901234567890.123", "currency": "JOD"}
    assert dumped["items"][0]["unit_price"]["amount"] == "0.1000000000000000055511151231"
    assert restored.items[1].unit_price.amount == Decimal("-2.005")
    # UUIDs, enums, references and offsets survive.
    assert restored.id == original.id and restored.customer_id == original.customer_id
    assert dumped["status"] == "fulfilled" and restored.status is OrderStatus.FULFILLED
    assert restored.external_refs == REFS and restored.items[1].external_refs == REFS
    assert restored.created_at.utcoffset() == timedelta(hours=2)
    assert restored.created_at.microsecond == 123456
    assert isinstance(restored.items, tuple)
