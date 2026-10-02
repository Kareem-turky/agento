"""Trusted governed actions of Workflow inspection (immutable constants).

Both require the ``workflows.read`` Product permission and are COMPANY scoped READ
actions. There is deliberately no ``workflows.run`` action: Workflows are executed only by
trusted Product services (for example the daily operations report), never over a generic
HTTP endpoint.
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "workflows.read"


def _read(name: str, description: str) -> ActionDefinition:
    return ActionDefinition(name=name, description=description, risk=ActionRisk.READ,
                            required_permission=READ_PERMISSION,
                            scope_requirement=ActionScopeRequirement.COMPANY)  # fmt: skip


CATALOG_READ = _read("workflows.catalog.read", "Read the Product Workflow catalog.")
RUNS_READ = _read("workflows.runs.read", "Read this company's Workflow run history.")

WORKFLOW_ACTIONS: tuple[ActionDefinition, ...] = (CATALOG_READ, RUNS_READ)
