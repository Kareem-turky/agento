import json

import pytest
from pydantic import ValidationError

from app.governance import (
    ActionCatalog,
    ActionDefinition,
    ActionIntent,
    ActionRisk,
    ActionScope,
    ActionScopeRequirement,
)
from tests.governance.factories import CATALOG, definition


def test_enums() -> None:
    assert [r.value for r in ActionRisk] == ["read", "low_risk_write", "medium_risk", "high_risk"]
    assert {s.value for s in ActionScopeRequirement} == {"company", "store"}


def test_definition_is_frozen_and_strict() -> None:
    action = definition("orders.read", ActionRisk.READ, ActionScopeRequirement.STORE)
    assert action.approval_required is False
    with pytest.raises(ValidationError):
        action.risk = ActionRisk.HIGH_RISK


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "orders"},  # needs a dotted name
        {"name": "Orders.Read"},
        {"name": "orders.*"},
        {"required_permission": "*"},
        {"required_permission": "orders.*"},
        {"required_permission": ""},
        {"risk": "critical"},
        {"scope_requirement": "tenant"},
        {"approval_required": "yes"},
        {"description": "  "},
        {"handler": "module.function"},
    ],
)
def test_definition_rejects_invalid_values(overrides: dict) -> None:
    data = {
        "name": "orders.read",
        "description": "Read orders",
        "risk": "read",
        "required_permission": "orders.read",
        "scope_requirement": "store",
        **overrides,
    }
    with pytest.raises(ValidationError):
        ActionDefinition(**data)


def test_catalog_lookup_is_exact_and_immutable() -> None:
    assert CATALOG.get("orders.read").risk is ActionRisk.READ
    assert CATALOG.get("orders.READ") is None
    assert CATALOG.get("orders") is None
    assert CATALOG.get("orders.read ") is None
    assert "orders.cancel" in CATALOG
    assert len(CATALOG) == 7
    with pytest.raises(TypeError):
        CATALOG._definitions["x.y"] = CATALOG.get("orders.read")  # type: ignore[index]


def test_catalog_rejects_duplicates_and_non_definitions() -> None:
    action = definition("orders.read", ActionRisk.READ, ActionScopeRequirement.STORE)
    with pytest.raises(ValueError, match="duplicate"):
        ActionCatalog([action, action])
    with pytest.raises(TypeError):
        ActionCatalog([{"name": "orders.read"}])  # type: ignore[list-item]


def test_intent_carries_only_the_action_name() -> None:
    intent = ActionIntent(action_name="orders.read")
    assert intent.model_dump() == {"action_name": "orders.read"}
    with pytest.raises(ValidationError):
        intent.action_name = "orders.refund"


@pytest.mark.parametrize(
    "smuggled",
    [
        {"risk": "read"},
        {"required_permission": "orders.read"},
        {"scope_requirement": "company"},
        {"approval_required": False},
        {"permissions": ["orders.refund"]},
        {"role_ids": ["admin"]},
        {"actor_id": "someone-else"},
        {"company_id": "other-company"},
        {"store_id": "store-b"},
    ],
)
def test_intent_cannot_supply_governing_fields(smuggled: dict) -> None:
    with pytest.raises(ValidationError):
        ActionIntent(action_name="orders.refund", **smuggled)
    with pytest.raises(ValidationError):
        ActionIntent.model_validate({"action_name": "orders.refund", **smuggled})
    with pytest.raises(ValidationError):
        ActionIntent.model_validate_json(json.dumps({"action_name": "orders.refund", **smuggled}))


@pytest.mark.parametrize("bad", [{"action_name": ""}, {"action_name": "  "}, {}])
def test_intent_requires_a_name(bad: dict) -> None:
    with pytest.raises(ValidationError):
        ActionIntent(**bad)


def test_scope_validation() -> None:
    assert ActionScope(company_id="c").store_id is None
    for bad in (
        {"company_id": ""},
        {"company_id": "c", "store_id": " "},
        {"company_id": "c", "x": 1},
    ):
        with pytest.raises(ValidationError):
            ActionScope(**bad)
