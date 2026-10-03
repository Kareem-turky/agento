"""``HumanApproverPermissionEvaluator``: the Product evaluator, except that a
``system_agent`` actor is NEVER granted ``approvals.decide`` or ``approvals.cancel``,
even when malformed permissions name them. An Agent may REQUEST an action that needs a
human decision (through governance); it never approves, rejects or cancels one. The
refusal goes through the normal gate, so an attempt is denied AND audited.
"""

from app.approval_management.actions import HUMAN_ONLY_PERMISSIONS
from app.context.models import ActorContext
from app.governance import ActionDefinition, ActionScope
from app.governance.permissions import PermissionDecision, PermissionEvaluator, PermissionReason

AGENT_ACTOR_TYPE = "system_agent"


class HumanApproverPermissionEvaluator(PermissionEvaluator):
    def evaluate(
        self, actor: ActorContext | None, action: ActionDefinition, scope: ActionScope
    ) -> PermissionDecision:
        if (
            actor is not None
            and actor.actor_type == AGENT_ACTOR_TYPE
            and action.required_permission in HUMAN_ONLY_PERMISSIONS
        ):
            return PermissionDecision(
                allowed=False,
                reason=PermissionReason.MISSING_PERMISSION,
                action_name=action.name,
                required_permission=action.required_permission,
            )
        return super().evaluate(actor, action, scope)
