from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.company.operating_model import (
    DEFAULT_TERMINAL_SHIPMENT_STATUSES,
    OrderSLAConfig,
    ShipmentSLAConfig,
)


def test_order_slas_are_optional_and_statuses_empty_by_default() -> None:
    config = OrderSLAConfig()

    assert config.confirmation_sla is None
    assert config.processing_sla is None
    assert config.fulfillment_sla is None
    assert config.late_order_statuses == frozenset()


def test_order_sla_values_and_status_set() -> None:
    config = OrderSLAConfig(
        processing_sla=86400, late_order_statuses=["processing", "pending", "processing"]
    )

    assert config.processing_sla == timedelta(hours=24)
    assert isinstance(config.late_order_statuses, frozenset)
    assert config.late_order_statuses == {OrderStatus.PENDING, OrderStatus.PROCESSING}


def test_order_status_set_serializes_sorted() -> None:
    config = OrderSLAConfig(late_order_statuses=["processing", "confirmed", "pending"])

    assert config.model_dump(mode="json")["late_order_statuses"] == [
        "confirmed", "pending", "processing",
    ]  # fmt: skip


@pytest.mark.parametrize("bad", [["shipped"], ["PROCESSING"], ["late"]])
def test_order_statuses_must_be_canonical(bad: list[str]) -> None:
    with pytest.raises(ValidationError):
        OrderSLAConfig(late_order_statuses=bad)


def test_order_sla_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError):
        OrderSLAConfig(processing_sla=0)


def test_shipment_terminal_statuses_default_is_documented_set() -> None:
    config = ShipmentSLAConfig()

    assert config.terminal_statuses == DEFAULT_TERMINAL_SHIPMENT_STATUSES
    assert DEFAULT_TERMINAL_SHIPMENT_STATUSES == {
        ShipmentStatus.DELIVERED, ShipmentStatus.RETURNED, ShipmentStatus.CANCELLED,
    }  # fmt: skip
    assert config.ready_to_ship_sla is None
    assert config.ship_to_delivery_sla is None


def test_shipment_terminal_statuses_are_configurable() -> None:
    config = ShipmentSLAConfig(terminal_statuses=["delivered", "failed"], ready_to_ship_sla=3600)

    assert config.terminal_statuses == {ShipmentStatus.DELIVERED, ShipmentStatus.FAILED}
    assert config.ready_to_ship_sla == timedelta(hours=1)
    assert config.model_dump(mode="json")["terminal_statuses"] == ["delivered", "failed"]


def test_shipment_statuses_must_be_canonical() -> None:
    with pytest.raises(ValidationError):
        ShipmentSLAConfig(terminal_statuses=["processing"])


@pytest.mark.parametrize("model", [OrderSLAConfig, ShipmentSLAConfig])
def test_sla_configs_are_frozen_and_forbid_unknown_fields(model: type) -> None:
    with pytest.raises(ValidationError):
        model(lateness_formula="now - created_at")
    config = model()
    with pytest.raises(ValidationError):
        config.__setattr__(next(iter(model.model_fields)), None)
