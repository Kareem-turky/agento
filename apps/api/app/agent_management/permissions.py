"""Agent management is for human operators and API clients, never for Agents.

``HumanOperatorPermissionEvaluator`` is the governance ``PermissionEvaluator`` of the
Agent-management actions: it behaves exactly like the Product evaluator, except that a
``system_agent`` actor is never granted an Agent-management action, even if its
permissions name it. The refusal goes through the normal gate, so a mutation attempt is
denied AND audited by ``ExecutionCoordinator``. An Agent therefore cannot enable,
disable or reset itself or any other Agent.
"""

from app.context.models import ActorContext
from app.governance import ActionDefinition, ActionScope
from app.governance.permissions import PermissionDecision, PermissionEvaluator, PermissionReason

AGENT_ACTOR_TYPE = "system_agent"


class HumanOperatorPermissionEvaluator(PermissionEvaluator):
    def evaluate(
        self, actor: ActorContext | None, action: ActionDefinition, scope: ActionScope
    ) -> PermissionDecision:
        if actor is not None and actor.actor_type == AGENT_ACTOR_TYPE:
            return PermissionDecision(
                allowed=False,
                reason=PermissionReason.MISSING_PERMISSION,
                action_name=action.name,
                required_permission=action.required_permission,
            )
        return super().evaluate(actor, action, scope)
