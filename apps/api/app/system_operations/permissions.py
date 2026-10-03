"""``system.read``: the ONLY System permission (Task 039).

System Status is installation-level, read-only operational data. There is deliberately
no system write, restart, backup or restore permission: those are operator deployment
and CLI concerns, never Product API actions.
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

SYSTEM_READ_PERMISSION = "system.read"

SYSTEM_READ = ActionDefinition(
    name="system.read",
    description="Read this installation's operational System Status.",
    risk=ActionRisk.READ,
    required_permission=SYSTEM_READ_PERMISSION,
    scope_requirement=ActionScopeRequirement.COMPANY,
)

SYSTEM_ACTIONS: tuple[ActionDefinition, ...] = (SYSTEM_READ,)
