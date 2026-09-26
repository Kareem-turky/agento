"""Order invariants."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.commerce.domain import OrderStatus
from tests.commerce.factories import CREATED_AT, item, money, order


def test_valid_order() -> None:
    customer_id = uuid4()
    built = order(
        customer_id=customer_id,
        items=(
            item(quantity=Decimal("2"), sku="SKU-1", variant_id=uuid4()),
            item(title="Manual line"),
        ),
    )

    assert built.status is OrderStatus.PENDING
    assert built.customer_id == customer_id
    assert len(built.items) == 2
    assert built.items[1].variant_id is None  # custom/manual items need no variant


def test_empty_items_rejected() -> None:
    with pytest.raises(ValidationError):
        order(items=())


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1"), "-0.5", 0])
def test_non_positive_quantity_rejected(quantity) -> None:
    with pytest.raises(ValidationError):
        item(quantity=quantity)


def test_fractional_quantity_accepted() -> None:
    assert item(quantity="0.25").quantity == Decimal("0.25")


def test_float_quantity_rejected() -> None:
    with pytest.raises(ValidationError):
        item(quantity=1.5)


def test_item_order_currency_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="order total currency"):
        order(items=(item(unit_price=money("5", "EUR")),), total=money("5", "USD"))


def test_mixed_item_currencies_rejected() -> None:
    with pytest.raises(ValidationError):
        order(items=(item(), item(unit_price=money("1", "EUR"))), total=money("1", "USD"))


def test_currency_comparison_uses_normalized_codes() -> None:
    built = order(items=(item(unit_price=money("1", "usd")),), total=money("1", "USD"))

    assert built.total.currency == "USD"


def test_total_is_not_derived_from_item_prices() -> None:
    # Discounts, tax, shipping and adjustments make "total == sum(lines)" unsafe.
    built = order(
        items=(item(quantity=Decimal("2"), unit_price=money("10.00")),), total=money("17.35")
    )

    assert built.total.amount == Decimal("17.35")


def test_source_status_is_kept_separately_from_canonical_status() -> None:
    built = order(status=OrderStatus.PROCESSING, source_status=" awaiting_courier_pickup ")

    assert built.status is OrderStatus.PROCESSING
    assert built.source_status == "awaiting_courier_pickup"


def test_unknown_canonical_status_rejected() -> None:
    with pytest.raises(ValidationError):
        order(status="awaiting_courier_pickup")


def test_status_set_is_canonical() -> None:
    assert {s.value for s in OrderStatus} == {
        "draft", "pending", "confirmed", "processing",
        "fulfilled", "cancelled", "completed", "unknown",
    }  # fmt: skip


def test_items_are_an_immutable_tuple() -> None:
    built = order(items=[item(), item()])

    assert isinstance(built.items, tuple)
    with pytest.raises(AttributeError):
        built.items.append(item())  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        built.items = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        built.items[0].quantity = Decimal("9")  # type: ignore[misc]


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_naive_timestamps_rejected(field) -> None:
    with pytest.raises(ValidationError):
        order(**{field: datetime(2026, 1, 15, 10, 30)})


def test_timezone_aware_timestamps_accepted() -> None:
    cairo = timezone(timedelta(hours=2))
    built = order(created_at=datetime(2026, 1, 15, 12, 30, tzinfo=cairo), updated_at=CREATED_AT)

    assert built.created_at == CREATED_AT  # same instant, different offset
    assert built.created_at.utcoffset() == timedelta(hours=2)
    assert built.updated_at.tzinfo is UTC
