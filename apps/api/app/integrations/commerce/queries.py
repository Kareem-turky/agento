"""Typed, immutable read queries for the commerce integration contract.

Semantics (every adapter must follow them):
- An empty ``statuses`` set means "any status"; otherwise canonical statuses only.
- Time ranges are half-open: ``from <= value < to``. ``from`` must be before ``to``.
- For shipments, a time bound only matches shipments that have ``shipped_at``.
- ``limit`` (1..MAX_QUERY_LIMIT) is applied after filtering and sorting.
- Orders sort by ``created_at`` ascending, then ``id``. Shipments sort by
  ``shipped_at`` ascending (unshipped last), then ``id``.
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, StrictInt, model_validator

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.commerce.domain.common import DOMAIN_MODEL_CONFIG

MAX_QUERY_LIMIT = 500


def _check_range(start: datetime | None, end: datetime | None, name: str) -> None:
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{name}_from must be before {name}_to (the range is half-open)")


class OrderQuery(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    store_id: UUID | None = None
    statuses: frozenset[OrderStatus] = frozenset()
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    limit: StrictInt | None = Field(default=None, ge=1, le=MAX_QUERY_LIMIT)

    @model_validator(mode="after")
    def _valid_range(self) -> Self:
        _check_range(self.created_from, self.created_to, "created")
        return self


class ShipmentQuery(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    order_id: UUID | None = None
    statuses: frozenset[ShipmentStatus] = frozenset()
    shipped_from: AwareDatetime | None = None
    shipped_to: AwareDatetime | None = None
    limit: StrictInt | None = Field(default=None, ge=1, le=MAX_QUERY_LIMIT)

    @model_validator(mode="after")
    def _valid_range(self) -> Self:
        _check_range(self.shipped_from, self.shipped_to, "shipped")
        return self
