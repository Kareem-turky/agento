"""Permission evaluation: may this trusted actor perform this action on this target?

Rules (fail closed):
- no actor -> denied; unknown action name -> denied;
- the actor's company must equal the target company;
- the target must match the action's scope requirement (STORE needs a store id,
  COMPANY must not have one);
- the actor must hold the action's required permission exactly: no wildcards, no
  prefix matching, and roles grant nothing by themselves;
- STORE-scoped actions need the target store in ``actor.store_ids`` (empty -> none).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, StrictBool

from app.context.models import ActorContext
from app.governance.actions import (
    ActionCatalog,
    ActionDefinition,
    ActionIntent,
    ActionScope,
    ActionScopeRequirement,
)


class PermissionReason(StrEnum):
    GRANTED = "granted"
    NO_ACTOR = "no_actor"
    UNKNOWN_ACTION = "unknown_action"
    COMPANY_MISMATCH = "company_mismatch"
    STORE_SCOPE_MISSING = "store_scope_missing"
    UNEXPECTED_STORE_SCOPE = "unexpected_store_scope"
    MISSING_PERMISSION = "missing_permission"
    STORE_NOT_PERMITTED = "store_not_permitted"


class PermissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: StrictBool
    reason: PermissionReason
    action_name: str
    # The trusted definition, when the action is known.
    action: ActionDefinition | None = None


class PermissionEvaluator:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def evaluate(
        self, actor: ActorContext | None, intent: ActionIntent, scope: ActionScope
    ) -> PermissionDecision:
        action = self._catalog.get(intent.action_name)

        def deny(reason: PermissionReason) -> PermissionDecision:
            return PermissionDecision(
                allowed=False, reason=reason, action_name=intent.action_name, action=action
            )

        if actor is None:
            return deny(PermissionReason.NO_ACTOR)
        if action is None:
            return deny(PermissionReason.UNKNOWN_ACTION)
        if actor.company_id != scope.company_id:
            return deny(PermissionReason.COMPANY_MISMATCH)
        is_store_action = action.scope_requirement is ActionScopeRequirement.STORE
        if is_store_action and scope.store_id is None:
            return deny(PermissionReason.STORE_SCOPE_MISSING)
        if not is_store_action and scope.store_id is not None:
            return deny(PermissionReason.UNEXPECTED_STORE_SCOPE)
        if action.required_permission not in actor.permissions:
            return deny(PermissionReason.MISSING_PERMISSION)
        if is_store_action and scope.store_id not in actor.store_ids:
            return deny(PermissionReason.STORE_NOT_PERMITTED)
        return PermissionDecision(
            allowed=True,
            reason=PermissionReason.GRANTED,
            action_name=intent.action_name,
            action=action,
        )
