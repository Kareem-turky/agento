"""Shared trusted fixtures for governance tests."""

from typing import Any

from app.context.models import ActorContext
from app.governance import (
    ActionCatalog,
    ActionDefinition,
    ActionRisk,
    ActionScope,
    ActionScopeRequirement,
)

COMPANY = "company-1"
STORE_A = "store-a"
STORE_B = "store-b"


def definition(name: str, risk: ActionRisk, scope: ActionScopeRequirement, **extra: Any):
    return ActionDefinition(
        name=name,
        description=f"{name} (test)",
        risk=risk,
        required_permission=extra.pop("required_permission", name),
        scope_requirement=scope,
        **extra,
    )


CATALOG = ActionCatalog(
    [
        definition("orders.read", ActionRisk.READ, ActionScopeRequirement.STORE),
        definition("orders.add_note", ActionRisk.LOW_RISK_WRITE, ActionScopeRequirement.STORE),
        definition("orders.cancel", ActionRisk.MEDIUM_RISK, ActionScopeRequirement.STORE),
        definition("orders.refund", ActionRisk.HIGH_RISK, ActionScopeRequirement.STORE),
        definition("reports.read", ActionRisk.READ, ActionScopeRequirement.COMPANY),
        definition("settings.update", ActionRisk.HIGH_RISK, ActionScopeRequirement.COMPANY),
        definition(
            "orders.tag",
            ActionRisk.LOW_RISK_WRITE,
            ActionScopeRequirement.STORE,
            approval_required=True,
        ),
    ]
)


def actor(**overrides: Any) -> ActorContext:
    data: dict[str, Any] = {
        "actor_id": "user-1",
        "actor_type": "user",
        "company_id": COMPANY,
        "permissions": frozenset(CATALOG.names),
        "store_ids": frozenset({STORE_A}),
    }
    data.update(overrides)
    return ActorContext(**data)


def store_scope(store_id: str = STORE_A, company_id: str = COMPANY) -> ActionScope:
    return ActionScope(company_id=company_id, store_id=store_id)


def company_scope(company_id: str = COMPANY) -> ActionScope:
    return ActionScope(company_id=company_id)
