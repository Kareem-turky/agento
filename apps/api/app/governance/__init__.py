"""Governance decisions: which actions a trusted actor may take, and on what terms.

Pure decision logic. It decides; it never executes an action, calls an integration,
persists an approval or writes an audit record.

    trusted ActorContext + untrusted ActionIntent + trusted ActionScope
        -> trusted ActionCatalog (definition, risk, required permission)
        -> PermissionEvaluator  -> PermissionDecision
        -> BaselinePolicyEvaluator -> PolicyDecision (ALLOW / DENY / REQUIRE_APPROVAL)
"""

from app.governance.actions import (
    ActionCatalog,
    ActionDefinition,
    ActionIntent,
    ActionRisk,
    ActionScope,
    ActionScopeRequirement,
)
from app.governance.gate import GovernanceGate
from app.governance.permissions import PermissionDecision, PermissionEvaluator, PermissionReason
from app.governance.policy import (
    BaselinePolicyEvaluator,
    PolicyDecision,
    PolicyOutcome,
    PolicyReason,
)

__all__ = [
    "ActionCatalog",
    "ActionDefinition",
    "ActionIntent",
    "ActionRisk",
    "ActionScope",
    "ActionScopeRequirement",
    "BaselinePolicyEvaluator",
    "GovernanceGate",
    "PermissionDecision",
    "PermissionEvaluator",
    "PermissionReason",
    "PolicyDecision",
    "PolicyOutcome",
    "PolicyReason",
]
