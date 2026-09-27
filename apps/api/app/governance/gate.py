"""GovernanceGate: the single entry point that composes permission and policy.

It returns a ``PolicyDecision`` and stops there: executing the action, requesting
approval and auditing are later layers.
"""

from app.context.models import ActorContext
from app.governance.actions import ActionCatalog, ActionIntent, ActionScope
from app.governance.permissions import PermissionEvaluator
from app.governance.policy import BaselinePolicyEvaluator, PolicyDecision


class GovernanceGate:
    def __init__(
        self, catalog: ActionCatalog, policy: BaselinePolicyEvaluator | None = None
    ) -> None:
        self._permissions = PermissionEvaluator(catalog)
        self._policy = policy if policy is not None else BaselinePolicyEvaluator()

    def decide(
        self, actor: ActorContext | None, intent: ActionIntent, scope: ActionScope
    ) -> PolicyDecision:
        return self._policy.evaluate(self._permissions.evaluate(actor, intent, scope))
