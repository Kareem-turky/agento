"""Trusted definitions of the operations actions (immutable constants, no registry)."""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

CREATE_TICKET_ACTION = ActionDefinition(
    name="operations.ticket.create",
    description="Create an operational ticket for a store in the ticketing system.",
    risk=ActionRisk.LOW_RISK_WRITE,
    required_permission="tickets.create",
    scope_requirement=ActionScopeRequirement.STORE,
)

ORDER_READ_ACTION = ActionDefinition(
    name="operations.order.read",
    description="Read one order of a store (operational snapshot).",
    risk=ActionRisk.READ,
    required_permission="orders.read",
    scope_requirement=ActionScopeRequirement.STORE,
)

SHIPMENTS_READ_ACTION = ActionDefinition(
    name="operations.shipments.read",
    description="Read the shipments of one order of a store (operational snapshots).",
    risk=ActionRisk.READ,
    required_permission="shipments.read",
    scope_requirement=ActionScopeRequirement.STORE,
)

OPERATIONS_ACTIONS: tuple[ActionDefinition, ...] = (
    ORDER_READ_ACTION,
    SHIPMENTS_READ_ACTION,
    CREATE_TICKET_ACTION,
)
