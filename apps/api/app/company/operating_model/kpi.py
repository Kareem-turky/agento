"""Which operational KPIs matter to the company (definitions only; nothing is computed)."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, model_validator

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG
from app.company.operating_model.common import SortedJson


class KPIKey(StrEnum):
    ORDERS_CREATED = "orders_created"
    ORDERS_CONFIRMED = "orders_confirmed"
    ORDERS_FULFILLED = "orders_fulfilled"
    ORDERS_CANCELLED = "orders_cancelled"
    LATE_ORDERS = "late_orders"
    FULFILLMENT_RATE = "fulfillment_rate"
    SHIPMENTS_CREATED = "shipments_created"
    SHIPMENTS_DELIVERED = "shipments_delivered"
    LATE_SHIPMENTS = "late_shipments"
    INVENTORY_LOW_ITEMS = "inventory_low_items"


class KPIConfig(BaseModel):
    """``primary_kpis`` is ordered (display priority), duplicate-free and a subset of
    ``enabled_kpis``."""

    model_config = DOMAIN_MODEL_CONFIG

    enabled_kpis: Annotated[frozenset[KPIKey], SortedJson] = frozenset()
    primary_kpis: tuple[KPIKey, ...] = ()

    @model_validator(mode="after")
    def _primary_kpis_are_unique_and_enabled(self) -> Self:
        if len(set(self.primary_kpis)) != len(self.primary_kpis):
            raise ValueError("primary_kpis must not contain duplicates")
        missing = sorted(k.value for k in set(self.primary_kpis) - self.enabled_kpis)
        if missing:
            raise ValueError(f"primary_kpis must also be enabled; not enabled: {missing}")
        return self
