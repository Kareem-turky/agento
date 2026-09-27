"""Permission evaluation: may this trusted actor perform this trusted action on this target?

The evaluator only sees a trusted ``ActionDefinition``. Resolving an untrusted action
name to a definition (and denying unknown names) happens earlier, in the gate.

Checks, in order (fail closed, first failure wins):
1. no actor                                   -> NO_ACTOR
2. actor.company_id != scope.company_id       -> COMPANY_MISMATCH
3. exact required permission not held         -> MISSING_PERMISSION
   (no wildcards, no prefix matching; roles and actor type grant nothing)
4. STORE actions only:
   a. scope has no store                      -> STORE_SCOPE_MISSING
   b. store not in actor.store_ids            -> STORE_NOT_PERMITTED (empty -> none)
5.                                            -> GRANTED
COMPANY actions are not constrained by stores: neither ``scope.store_id`` nor
``actor.store_ids`` affects them.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, StrictBool

from app.context.models import ActorContext
from app.governance.actions import ActionDefinition, ActionScope, ActionScopeRequirement


class PermissionReason(StrEnum):
    GRANTED = "granted"
    NO_ACTOR = "no_actor"
    COMPANY_MISMATCH = "company_mismatch"
    MISSING_PERMISSION = "missing_permission"
    STORE_SCOPE_MISSING = "store_scope_missing"
    STORE_NOT_PERMITTED = "store_not_permitted"


class PermissionDecision(BaseModel):
    """The authorization result for one trusted action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: StrictBool
    reason: PermissionReason
    action_name: str
    required_permission: str


class PermissionEvaluator:
    def evaluate(
        self, actor: ActorContext | None, action: ActionDefinition, scope: ActionScope
    ) -> PermissionDecision:
        def decision(reason: PermissionReason) -> PermissionDecision:
            return PermissionDecision(
                allowed=reason is PermissionReason.GRANTED,
                reason=reason,
                action_name=action.name,
                required_permission=action.required_permission,
            )

        if actor is None:
            return decision(PermissionReason.NO_ACTOR)
        if actor.company_id != scope.company_id:
            return decision(PermissionReason.COMPANY_MISMATCH)
        if action.required_permission not in actor.permissions:
            return decision(PermissionReason.MISSING_PERMISSION)
        if action.scope_requirement is ActionScopeRequirement.STORE:
            if scope.store_id is None:
                return decision(PermissionReason.STORE_SCOPE_MISSING)
            if scope.store_id not in actor.store_ids:
                return decision(PermissionReason.STORE_NOT_PERMITTED)
        return decision(PermissionReason.GRANTED)
