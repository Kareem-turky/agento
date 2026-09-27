"""Baseline policy: turn a permission decision into ALLOW, DENY or REQUIRE_APPROVAL.

- permission denied            -> DENY (never REQUIRE_APPROVAL)
- READ / LOW_RISK_WRITE        -> ALLOW, unless the action demands approval
- MEDIUM_RISK / HIGH_RISK      -> REQUIRE_APPROVAL

Company-specific policy may later make this stricter; nothing here loosens it.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.governance.actions import ActionRisk
from app.governance.permissions import PermissionDecision


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyReason(StrEnum):
    PERMISSION_DENIED = "permission_denied"
    READ_ALLOWED = "read_allowed"
    LOW_RISK_WRITE_ALLOWED = "low_risk_write_allowed"
    ACTION_REQUIRES_APPROVAL = "action_requires_approval"
    MEDIUM_RISK_REQUIRES_APPROVAL = "medium_risk_requires_approval"
    HIGH_RISK_REQUIRES_APPROVAL = "high_risk_requires_approval"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: PolicyOutcome
    reason: PolicyReason
    action_name: str
    risk: ActionRisk | None
    permission: PermissionDecision


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
    def evaluate(self, permission: PermissionDecision) -> PolicyDecision:
        action = permission.action
        if not permission.allowed or action is None:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.PERMISSION_DENIED,
                action_name=permission.action_name,
                risk=action.risk if action else None,
                permission=permission,
            )
        outcome, reason = _BY_RISK[action.risk]
        if outcome is PolicyOutcome.ALLOW and action.approval_required:
            outcome, reason = PolicyOutcome.REQUIRE_APPROVAL, PolicyReason.ACTION_REQUIRES_APPROVAL
        return PolicyDecision(
            outcome=outcome,
            reason=reason,
            action_name=permission.action_name,
            risk=action.risk,
            permission=permission,
        )
