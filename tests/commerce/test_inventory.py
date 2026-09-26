"""Inventory semantics are intentionally permissive."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.commerce.domain import InventoryLevel


def level(**overrides) -> InventoryLevel:
    return InventoryLevel(
        **{"variant_id": uuid4(), "warehouse_id": uuid4(), "available": Decimal("5"), **overrides}
    )


def test_decimal_quantities() -> None:
    inventory = level(available="2.5", on_hand="10.75", reserved="0.125")

    assert (inventory.available, inventory.on_hand, inventory.reserved) == (
        Decimal("2.5"),
        Decimal("10.75"),
        Decimal("0.125"),
    )


def test_negative_available_is_accepted() -> None:
    assert level(available=Decimal("-3")).available == Decimal("-3")


def test_on_hand_and_reserved_are_optional() -> None:
    inventory = level()

    assert inventory.on_hand is None and inventory.reserved is None


def test_available_is_required() -> None:
    with pytest.raises(ValidationError):
        InventoryLevel(variant_id=uuid4(), warehouse_id=uuid4())


def test_no_forced_available_equals_on_hand_minus_reserved() -> None:
    inventory = level(available=Decimal("7"), on_hand=Decimal("10"), reserved=Decimal("1"))

    assert inventory.available != inventory.on_hand - inventory.reserved


@pytest.mark.parametrize("value", [1.5, "NaN", "Infinity"])
def test_rejects_float_and_non_finite_quantities(value) -> None:
    with pytest.raises(ValidationError):
        level(available=value)


def test_naive_updated_at_rejected() -> None:
    with pytest.raises(ValidationError):
        level(updated_at=datetime(2026, 1, 1, 12, 0))


def test_aware_updated_at_accepted() -> None:
    stamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    assert level(updated_at=stamp).updated_at == stamp
