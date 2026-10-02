"""Trusted governed actions of Agent management (immutable constants).

Reads require ``agents.read``; mutations require ``agents.manage``. All are
COMPANY-scoped LOW_RISK_WRITE / READ actions; mutations run through
``ExecutionCoordinator`` and are therefore audited by the existing audit trail. No Agent
is given these permissions, and Agent actors are refused them even if granted (see
``HumanOperatorPermissionEvaluator``).
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "agents.read"
MANAGE_PERMISSION = "agents.manage"


def _action(name: str, description: str, *, write: bool) -> ActionDefinition:
    return ActionDefinition(
        name=name,
        description=description,
        risk=ActionRisk.LOW_RISK_WRITE if write else ActionRisk.READ,
        required_permission=MANAGE_PERMISSION if write else READ_PERMISSION,
        scope_requirement=ActionScopeRequirement.COMPANY,
    )


CATALOG_READ = _action("agents.catalog.read", "List the Product Agents installed in this build.",
                       write=False)  # fmt: skip
CONFIGURATION_READ = _action(
    "agents.configuration.read", "Read Agent configuration and effective state.", write=False
)
# Task 033: read-only inspection of the immutable Skill and Task catalogs (agents.read).
SKILLS_READ = _action("agents.skills.read", "Read the Product Skill catalog.", write=False)
TASKS_READ = _action("agents.tasks.read", "Read the Product Task catalog.", write=False)
AGENT_ENABLE = _action("agents.agent.enable", "Enable a Product Agent.", write=True)
AGENT_DISABLE = _action("agents.agent.disable", "Disable a Product Agent.", write=True)
CONFIGURATION_RESET = _action(
    "agents.configuration.reset", "Reset an Agent to its Product default configuration.", write=True
)

AGENT_MANAGEMENT_ACTIONS: tuple[ActionDefinition, ...] = (
    CATALOG_READ, CONFIGURATION_READ, SKILLS_READ, TASKS_READ, AGENT_ENABLE, AGENT_DISABLE,
    CONFIGURATION_RESET,
)  # fmt: skip
