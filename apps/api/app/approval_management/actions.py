"""Trusted governed actions of human-approval management (Task 036).

Reading needs ``approvals.read``; approving and rejecting need ``approvals.decide``;
cancelling needs ``approvals.cancel``. Decisions are Product-management LOW_RISK_WRITE
actions (they never require another approval: no recursion) run through
``ExecutionCoordinator`` and therefore audited. There is deliberately NO "create" action:
a request exists only because governance returned REQUIRE_APPROVAL for a real action.
An Agent actor is refused ``approvals.decide`` and ``approvals.cancel`` even if granted
(see ``HumanApproverPermissionEvaluator``).
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "approvals.read"
DECIDE_PERMISSION = "approvals.decide"
CANCEL_PERMISSION = "approvals.cancel"
HUMAN_ONLY_PERMISSIONS = frozenset({DECIDE_PERMISSION, CANCEL_PERMISSION})


def _action(name: str, description: str, permission: str, risk: ActionRisk) -> ActionDefinition:
    return ActionDefinition(
        name=name,
        description=description,
        risk=risk,
        required_permission=permission,
        scope_requirement=ActionScopeRequirement.COMPANY,
    )


REQUEST_READ = _action("approvals.request.read", "Read human approval requests.",
                       READ_PERMISSION, ActionRisk.READ)  # fmt: skip
REQUEST_APPROVE = _action("approvals.request.approve", "Approve a pending request.",
                          DECIDE_PERMISSION, ActionRisk.LOW_RISK_WRITE)  # fmt: skip
REQUEST_REJECT = _action("approvals.request.reject", "Reject a pending request.",
                         DECIDE_PERMISSION, ActionRisk.LOW_RISK_WRITE)  # fmt: skip
REQUEST_CANCEL = _action("approvals.request.cancel", "Cancel a pending request.",
                         CANCEL_PERMISSION, ActionRisk.LOW_RISK_WRITE)  # fmt: skip

APPROVAL_ACTIONS: tuple[ActionDefinition, ...] = (
    REQUEST_READ, REQUEST_APPROVE, REQUEST_REJECT, REQUEST_CANCEL,
)  # fmt: skip
