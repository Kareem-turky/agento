"""Declarative escalation rules.

A rule names a product-defined condition and a typed threshold. There are no
expressions, code, prompts or free-form parameters; a future evaluator interprets
the rule. Nothing is evaluated here.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StrictBool, StrictInt, StringConstraints, model_validator

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, DecimalValue, NonEmptyStr
from app.company.operating_model.common import Duration


class EscalationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EscalationCondition(StrEnum):
    """Stable, product-defined condition keys."""

    ORDER_LATE = "order.late"
    SHIPMENT_LATE = "shipment.late"
    INVENTORY_LOW = "inventory.low"


class CountThreshold(BaseModel):
    """Escalate when at least ``value`` matching items exist (e.g. 5 late orders)."""

    model_config = DOMAIN_MODEL_CONFIG
    kind: Literal["count"] = "count"
    value: StrictInt = Field(ge=1)


class DurationThreshold(BaseModel):
    """Escalate when an item exceeds its SLA by at least ``value``."""

    model_config = DOMAIN_MODEL_CONFIG
    kind: Literal["duration"] = "duration"
    value: Duration


class QuantityThreshold(BaseModel):
    """Escalate when a quantity is at or below ``value`` (e.g. available stock)."""

    model_config = DOMAIN_MODEL_CONFIG
    kind: Literal["quantity"] = "quantity"
    value: DecimalValue


Threshold = Annotated[
    CountThreshold | DurationThreshold | QuantityThreshold, Field(discriminator="kind")
]

# Which threshold kinds make sense for each condition.
ALLOWED_THRESHOLDS: dict[EscalationCondition, frozenset[str]] = {
    EscalationCondition.ORDER_LATE: frozenset({"count", "duration"}),
    EscalationCondition.SHIPMENT_LATE: frozenset({"count", "duration"}),
    EscalationCondition.INVENTORY_LOW: frozenset({"quantity"}),
}

# Stable machine identifier, e.g. "late-orders-critical".
RuleId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]


class EscalationRule(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: RuleId
    name: NonEmptyStr
    severity: EscalationSeverity
    enabled: StrictBool = True
    condition_key: EscalationCondition
    threshold: Threshold

    @model_validator(mode="after")
    def _threshold_fits_condition(self) -> Self:
        allowed = ALLOWED_THRESHOLDS[self.condition_key]
        if self.threshold.kind not in allowed:
            raise ValueError(
                f"{self.condition_key.value} supports {sorted(allowed)} thresholds, "
                f"not {self.threshold.kind!r}"
            )
        return self
