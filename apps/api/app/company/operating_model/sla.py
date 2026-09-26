"""Order and shipment SLA configuration (configuration only; nothing is evaluated here)."""

from typing import Annotated

from pydantic import BaseModel

from app.commerce.domain import OrderStatus, ShipmentStatus
from app.commerce.domain.common import DOMAIN_MODEL_CONFIG
from app.company.operating_model.common import Duration, SortedJson

OrderStatuses = Annotated[frozenset[OrderStatus], SortedJson]
ShipmentStatuses = Annotated[frozenset[ShipmentStatus], SortedJson]


class OrderSLAConfig(BaseModel):
    """How long an order may spend in each stage. ``None`` means the company does not
    enforce that SLA. ``late_order_statuses`` lists the canonical statuses to which
    lateness rules apply; empty by default so no policy is assumed."""

    model_config = DOMAIN_MODEL_CONFIG

    confirmation_sla: Duration | None = None
    processing_sla: Duration | None = None
    fulfillment_sla: Duration | None = None
    late_order_statuses: OrderStatuses = frozenset()


# Documented default: shipments in these statuses are finished and never "late".
# Companies may override it; nothing else in the platform assumes this set.
DEFAULT_TERMINAL_SHIPMENT_STATUSES: frozenset[ShipmentStatus] = frozenset(
    {ShipmentStatus.DELIVERED, ShipmentStatus.RETURNED, ShipmentStatus.CANCELLED}
)


class ShipmentSLAConfig(BaseModel):
    """How long a shipment may take per stage (``None`` = not enforced) and which
    canonical statuses the company treats as terminal."""

    model_config = DOMAIN_MODEL_CONFIG

    ready_to_ship_sla: Duration | None = None
    ship_to_delivery_sla: Duration | None = None
    terminal_statuses: ShipmentStatuses = DEFAULT_TERMINAL_SHIPMENT_STATUSES
