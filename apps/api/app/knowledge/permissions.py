"""``KnowledgePermissionEvaluator``: the Product evaluator, except that a
``system_agent`` actor is never granted ``knowledge.manage``, even if its permissions
name it (Agents never author company Knowledge). Reads are NOT globally denied to
Agents: an Agent holding ``knowledge.read`` may read, exactly like any actor. The
refusal goes through the normal gate, so a mutation attempt is denied AND audited.
"""

from app.context.models import ActorContext
from app.governance import ActionDefinition, ActionScope
from app.governance.permissions import PermissionDecision, PermissionEvaluator, PermissionReason
from app.knowledge.actions import MANAGE_PERMISSION

AGENT_ACTOR_TYPE = "system_agent"


class KnowledgePermissionEvaluator(PermissionEvaluator):
    def evaluate(
        self, actor: ActorContext | None, action: ActionDefinition, scope: ActionScope
    ) -> PermissionDecision:
        if (
            actor is not None
            and actor.actor_type == AGENT_ACTOR_TYPE
            and action.required_permission == MANAGE_PERMISSION
        ):
            return PermissionDecision(
                allowed=False,
                reason=PermissionReason.MISSING_PERMISSION,
                action_name=action.name,
                required_permission=action.required_permission,
            )
        return super().evaluate(actor, action, scope)
