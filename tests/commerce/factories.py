"""Small builders for valid canonical domain objects (test data only)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.commerce.domain import Money, Order, OrderItem, OrderStatus

CREATED_AT = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def money(amount: str = "10.00", currency: str = "USD") -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def item(**overrides) -> OrderItem:
    fields = {
        "title": "Test item",
        "quantity": Decimal("1"),
        "unit_price": money(),
    }
    return OrderItem(**{**fields, **overrides})


def order(**overrides) -> Order:
    fields = {
        "store_id": uuid4(),
        "status": OrderStatus.PENDING,
        "items": (item(),),
        "total": money("12.50"),
        "created_at": CREATED_AT,
    }
    return Order(**{**fields, **overrides})
