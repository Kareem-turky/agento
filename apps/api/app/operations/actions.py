"""Trusted definitions of the operations actions (immutable constants, no registry)."""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

CREATE_TICKET_ACTION = ActionDefinition(
    name="operations.ticket.create",
    description="Create an operational ticket for a store in the ticketing system.",
    risk=ActionRisk.LOW_RISK_WRITE,
    required_permission="tickets.create",
    scope_requirement=ActionScopeRequirement.STORE,
)

OPERATIONS_ACTIONS: tuple[ActionDefinition, ...] = (CREATE_TICKET_ACTION,)
