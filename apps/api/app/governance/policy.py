"""Baseline policy: turn a permission decision into ALLOW, DENY or REQUIRE_APPROVAL.

One deterministic baseline, driven only by the trusted action's risk:

    permission denied            -> DENY (never REQUIRE_APPROVAL)
    READ                         -> ALLOW
    LOW_RISK_WRITE               -> ALLOW
    MEDIUM_RISK                  -> REQUIRE_APPROVAL
    HIGH_RISK                    -> REQUIRE_APPROVAL

Unknown actions are denied by the gate (``UNKNOWN_ACTION``) before any of this runs.
There is no per-action or company-specific override yet.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.governance.actions import ActionDefinition, ActionRisk
from app.governance.permissions import PermissionDecision


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyReason(StrEnum):
    UNKNOWN_ACTION = "unknown_action"
    PERMISSION_DENIED = "permission_denied"
    READ_ALLOWED = "read_allowed"
    LOW_RISK_WRITE_ALLOWED = "low_risk_write_allowed"
    MEDIUM_RISK_REQUIRES_APPROVAL = "medium_risk_requires_approval"
    HIGH_RISK_REQUIRES_APPROVAL = "high_risk_requires_approval"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: PolicyOutcome
    reason: PolicyReason
    action_name: str
    # None when the action is unknown: no trusted definition, no risk, no permission check.
    risk: ActionRisk | None
    permission: PermissionDecision | None


_BY_RISK: dict[ActionRisk, tuple[PolicyOutcome, PolicyReason]] = {
    ActionRisk.READ: (PolicyOutcome.ALLOW, PolicyReason.READ_ALLOWED),
    ActionRisk.LOW_RISK_WRITE: (PolicyOutcome.ALLOW, PolicyReason.LOW_RISK_WRITE_ALLOWED),
    ActionRisk.MEDIUM_RISK: (
        PolicyOutcome.REQUIRE_APPROVAL,
        PolicyReason.MEDIUM_RISK_REQUIRES_APPROVAL,
    ),
    ActionRisk.HIGH_RISK: (
        PolicyOutcome.REQUIRE_APPROVAL,
        PolicyReason.HIGH_RISK_REQUIRES_APPROVAL,
    ),
}


class BaselinePolicyEvaluator:
    def evaluate(self, action: ActionDefinition, permission: PermissionDecision) -> PolicyDecision:
        if permission.action_name != action.name:
            raise ValueError("permission decision does not belong to this action")
        if permission.allowed:
            outcome, reason = _BY_RISK[action.risk]
        else:
            outcome, reason = PolicyOutcome.DENY, PolicyReason.PERMISSION_DENIED
        return PolicyDecision(
            outcome=outcome,
            reason=reason,
            action_name=action.name,
            risk=action.risk,
            permission=permission,
        )
