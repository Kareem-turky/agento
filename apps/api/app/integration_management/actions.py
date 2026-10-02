"""Trusted governed actions of integration management (immutable constants).

Reads require ``integrations.read``; every mutation requires ``integrations.manage``.
All are COMPANY-scoped (a connection belongs to the installation's company, not a
store). Mutations are LOW_RISK_WRITE: they run through ``ExecutionCoordinator`` and are
therefore audited (requested -> policy_decided -> execution -> verification). Integration
capabilities and categories never grant any of these permissions, and no Agent is
given them.
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "integrations.read"
MANAGE_PERMISSION = "integrations.manage"


def _action(name: str, description: str, *, write: bool) -> ActionDefinition:
    return ActionDefinition(
        name=name,
        description=description,
        risk=ActionRisk.LOW_RISK_WRITE if write else ActionRisk.READ,
        required_permission=MANAGE_PERMISSION if write else READ_PERMISSION,
        scope_requirement=ActionScopeRequirement.COMPANY,
    )


CATALOG_READ = _action(
    "integrations.catalog.read", "List the integration types installed in this build.", write=False
)
CONNECTIONS_READ = _action(
    "integrations.connections.read",
    "Read integration connection metadata (never secrets).",
    write=False,
)
CONNECTION_CREATE = _action(
    "integrations.connection.create", "Create an integration connection.", write=True
)
CONNECTION_UPDATE = _action(
    "integrations.connection.update",
    "Update a connection's name or non-secret configuration.",
    write=True,
)
CREDENTIALS_REPLACE = _action(
    "integrations.connection.credentials.replace", "Replace a connection's credentials.", write=True
)
CONNECTION_TEST = _action(
    "integrations.connection.test", "Test a connection and record the result.", write=True
)
CONNECTION_ENABLE = _action("integrations.connection.enable", "Enable a connection.", write=True)
CONNECTION_DISABLE = _action("integrations.connection.disable", "Disable a connection.", write=True)
CONNECTION_DELETE = _action("integrations.connection.delete",
                            "Delete a connection and its secret material.", write=True)  # fmt: skip

INTEGRATION_ACTIONS: tuple[ActionDefinition, ...] = (
    CATALOG_READ, CONNECTIONS_READ, CONNECTION_CREATE, CONNECTION_UPDATE, CREDENTIALS_REPLACE,
    CONNECTION_TEST, CONNECTION_ENABLE, CONNECTION_DISABLE, CONNECTION_DELETE,
)  # fmt: skip
