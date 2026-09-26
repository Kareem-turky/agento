from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.company.operating_model import (
    CountThreshold,
    DurationThreshold,
    EscalationCondition,
    EscalationRule,
    EscalationSeverity,
    QuantityThreshold,
)


def rule(**overrides: Any) -> EscalationRule:
    data: dict[str, Any] = {
        "id": "late-orders",
        "name": "Late orders",
        "severity": "warning",
        "condition_key": "order.late",
        "threshold": {"kind": "count", "value": 5},
    }
    data.update(overrides)
    return EscalationRule.model_validate(data)


def test_severities_and_conditions() -> None:
    assert [s.value for s in EscalationSeverity] == ["info", "warning", "critical"]
    assert {c.value for c in EscalationCondition} == {
        "order.late", "shipment.late", "inventory.low",
    }  # fmt: skip


def test_valid_rule_defaults_to_enabled() -> None:
    parsed = rule()

    assert parsed.enabled is True
    assert parsed.severity is EscalationSeverity.WARNING
    assert parsed.condition_key is EscalationCondition.ORDER_LATE
    assert parsed.threshold == CountThreshold(value=5)


@pytest.mark.parametrize(
    ("condition", "threshold", "expected"),
    [
        ("order.late", {"kind": "duration", "value": 3600}, DurationThreshold(value=3600)),
        ("shipment.late", {"kind": "count", "value": 1}, CountThreshold(value=1)),
        ("shipment.late", {"kind": "duration", "value": 86400}, DurationThreshold(value=86400)),
        ("inventory.low", {"kind": "quantity", "value": "10"}, QuantityThreshold(value="10")),
    ],
)
def test_threshold_kinds_per_condition(condition: str, threshold: dict, expected: object) -> None:
    assert rule(condition_key=condition, threshold=threshold).threshold == expected


def test_threshold_values_are_typed() -> None:
    assert rule(threshold={"kind": "duration", "value": 60}).threshold.value == timedelta(minutes=1)
    low = rule(condition_key="inventory.low", threshold={"kind": "quantity", "value": "2.5"})
    assert low.threshold.value == Decimal("2.5")


@pytest.mark.parametrize(
    ("condition", "threshold"),
    [
        ("order.late", {"kind": "quantity", "value": "1"}),
        ("shipment.late", {"kind": "quantity", "value": "1"}),
        ("inventory.low", {"kind": "count", "value": 1}),
        ("inventory.low", {"kind": "duration", "value": 60}),
    ],
)
def test_incompatible_threshold_kind_is_rejected(condition: str, threshold: dict) -> None:
    with pytest.raises(ValidationError):
        rule(condition_key=condition, threshold=threshold)


@pytest.mark.parametrize(
    "threshold",
    [
        {"kind": "count", "value": 0},
        {"kind": "count", "value": -1},
        {"kind": "count", "value": 1.0},
        {"kind": "count", "value": "5"},
        {"kind": "count", "value": True},
        {"kind": "duration", "value": 0},
        {"kind": "duration", "value": "1h"},
        {"kind": "expression", "value": "orders.late > 5"},
        {"kind": "count"},
        {"value": 5},
        {"kind": "count", "value": 5, "extra": 1},
    ],
)
def test_invalid_thresholds_are_rejected(threshold: dict) -> None:
    with pytest.raises(ValidationError):
        rule(threshold=threshold)


def test_quantity_threshold_rejects_float() -> None:
    with pytest.raises(ValidationError):
        rule(condition_key="inventory.low", threshold={"kind": "quantity", "value": 1.5})


@pytest.mark.parametrize(
    "overrides",
    [
        {"condition_key": "order.late || true"},
        {"condition_key": "custom.python"},
        {"severity": "urgent"},
        {"id": ""},
        {"id": "Late Orders"},
        {"id": "-late"},
        {"id": "x" * 65},
        {"name": "   "},
        {"enabled": "yes"},
        {"expression": "lambda order: order.late"},
        {"prompt": "Escalate when you think it's bad"},
        {"threshold": {"orders": 5}},
    ],
)
def test_invalid_rules_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        rule(**overrides)


def test_rule_is_frozen_and_round_trips() -> None:
    parsed = rule(threshold={"kind": "duration", "value": 7200}, enabled=False)
    with pytest.raises(ValidationError):
        parsed.enabled = True

    dumped = parsed.model_dump(mode="json")
    assert dumped["threshold"] == {"kind": "duration", "value": 7200}
    assert dumped["condition_key"] == "order.late"
    assert EscalationRule.model_validate(dumped) == parsed
