"""GovernanceGate: the single entry point for a governance decision.

    untrusted ActionIntent
      -> trusted catalog resolution (unknown name -> DENY / UNKNOWN_ACTION)
      -> trusted ActionDefinition
      -> PermissionEvaluator  -> PermissionDecision
      -> BaselinePolicyEvaluator -> PolicyDecision

It returns the decision and stops there: executing the action, requesting approval
and auditing are later layers.
"""

from app.context.models import ActorContext
from app.governance.actions import ActionCatalog, ActionIntent, ActionScope
from app.governance.permissions import PermissionEvaluator
from app.governance.policy import (
    BaselinePolicyEvaluator,
    PolicyDecision,
    PolicyOutcome,
    PolicyReason,
)


class GovernanceGate:
    def __init__(
        self,
        catalog: ActionCatalog,
        permissions: PermissionEvaluator | None = None,
        policy: BaselinePolicyEvaluator | None = None,
    ) -> None:
        self._catalog = catalog
        self._permissions = permissions if permissions is not None else PermissionEvaluator()
        self._policy = policy if policy is not None else BaselinePolicyEvaluator()

    def decide(
        self, actor: ActorContext | None, intent: ActionIntent, scope: ActionScope
    ) -> PolicyDecision:
        action = self._catalog.get(intent.name)
        if action is None:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.UNKNOWN_ACTION,
                action_name=intent.name,
                risk=None,
                permission=None,
            )
        permission = self._permissions.evaluate(actor, action, scope)
        return self._policy.evaluate(action, permission)
