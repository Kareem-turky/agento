"""Provider status vocabularies mapped onto canonical statuses.

Unknown provider values are not errors: they map to ``UNKNOWN`` and the exact
provider value is kept in ``source_status``. Matching is exact (no case folding).
"""

from app.commerce.domain import OrderStatus, ShipmentStatus, TicketStatus

ORDER_STATE_MAP: dict[str, OrderStatus] = {
    "open_draft": OrderStatus.DRAFT,
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.CONFIRMED,
    "packing": OrderStatus.PROCESSING,
    "handed_over": OrderStatus.FULFILLED,
    "closed": OrderStatus.COMPLETED,
    "voided": OrderStatus.CANCELLED,
}

DELIVERY_STATE_MAP: dict[str, ShipmentStatus] = {
    "label_created": ShipmentStatus.PENDING,
    "waiting_pickup": ShipmentStatus.READY,
    "picked_up": ShipmentStatus.SHIPPED,
    "moving": ShipmentStatus.IN_TRANSIT,
    "received": ShipmentStatus.DELIVERED,
    "delivery_failed": ShipmentStatus.FAILED,
    "sent_back": ShipmentStatus.RETURNED,
    "voided": ShipmentStatus.CANCELLED,
}


def map_order_state(state: object) -> OrderStatus:
    if isinstance(state, str):
        return ORDER_STATE_MAP.get(state, OrderStatus.UNKNOWN)
    return OrderStatus.UNKNOWN


def map_delivery_state(state: object) -> ShipmentStatus:
    if isinstance(state, str):
        return DELIVERY_STATE_MAP.get(state, ShipmentStatus.UNKNOWN)
    return ShipmentStatus.UNKNOWN


TICKET_STATE_MAP: dict[str, TicketStatus] = {
    "new": TicketStatus.OPEN,
    "solved": TicketStatus.RESOLVED,
    "withdrawn": TicketStatus.CANCELLED,
}


def map_ticket_state(state: object) -> TicketStatus:
    if isinstance(state, str):
        return TICKET_STATE_MAP.get(state, TicketStatus.UNKNOWN)
    return TicketStatus.UNKNOWN
