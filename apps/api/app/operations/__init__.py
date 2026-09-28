"""Business operations built on the governed execution foundation.

Product-owned business actions (their trusted definitions and handlers) live here,
outside the generic ``app.governance`` and ``app.execution`` packages. Every write
goes ExecutionCoordinator -> ActionHandler -> integration contract; nothing here
offers a path that skips governance.
"""

from app.operations.actions import (
    CREATE_TICKET_ACTION,
    OPERATIONS_ACTIONS,
    ORDER_READ_ACTION,
    SHIPMENTS_READ_ACTION,
)
from app.operations.tickets import (
    CreateOperationalTicketHandler,
    CreateOperationalTicketInput,
)

__all__ = [
    "CREATE_TICKET_ACTION",
    "OPERATIONS_ACTIONS",
    "ORDER_READ_ACTION",
    "SHIPMENTS_READ_ACTION",
    "CreateOperationalTicketHandler",
    "CreateOperationalTicketInput",
]
