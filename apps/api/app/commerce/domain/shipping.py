"""Shipments. Courier-specific statuses map to the canonical set; the original value is
kept in ``source_status``."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field

from app.commerce.domain.common import DOMAIN_MODEL_CONFIG, ExternalReferences, OptionalStr


class ShipmentStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Shipment(BaseModel):
    model_config = DOMAIN_MODEL_CONFIG

    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    status: ShipmentStatus
    source_status: OptionalStr = None
    courier_name: OptionalStr = None
    tracking_number: OptionalStr = None
    shipped_at: AwareDatetime | None = None
    delivered_at: AwareDatetime | None = None
    external_refs: ExternalReferences = frozenset()
