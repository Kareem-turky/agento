"""Orders and order items.

``Order.total`` is taken as reported and is NOT derived from item prices: real
orders include discounts, tax, shipping fees and manual adjustments.
"""

from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.commerce.domain.common import (
    DOMAIN_MODEL_CONFIG,
    DecimalValue,
    ExternalReferences,
    Money,
    NonEmptyStr,
    OptionalStr,
)


class OrderStatus(StrEnum):
    """Broad canonical status. Adapters map source values here and keep the original
    in ``source_status``."""

    DRAFT = "draft"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class OrderItem(BaseModel):
    """A line item. ``variant_id`` is optional: deleted, custom or manual items exist."""

    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    variant_id: UUID | None = None
    sku: OptionalStr = None
    title: NonEmptyStr
    quantity: DecimalValue = Field(gt=0)
    unit_price: Money
    external_refs: ExternalReferences = frozenset()


class Order(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    store_id: UUID
    customer_id: UUID | None = None
    status: OrderStatus
    source_status: OptionalStr = None
    items: tuple[OrderItem, ...] = Field(min_length=1)
    total: Money
    created_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    external_refs: ExternalReferences = frozenset()

    @model_validator(mode="after")
    def _items_share_the_order_currency(self) -> Self:
        currencies = {item.unit_price.currency for item in self.items}
        if currencies != {self.total.currency}:
            raise ValueError(
                "all item unit prices must use the order total currency "
                f"({self.total.currency}); found {sorted(currencies)}"
            )
        return self
