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

VALID = {
    "name": "orders.read",
    "description": "Read orders",
    "risk": "read",
    "required_permission": "orders.read",
    "scope_requirement": "store",
}


def test_enums() -> None:
    assert [r.value for r in ActionRisk] == ["read", "low_risk_write", "medium_risk", "high_risk"]
    assert {s.value for s in ActionScopeRequirement} == {"company", "store"}


def test_definition_fields_are_exactly_the_trusted_metadata() -> None:
    assert set(ActionDefinition.model_fields) == {
        "name", "description", "risk", "required_permission", "scope_requirement",
    }  # fmt: skip
    action = ActionDefinition(**VALID)
    with pytest.raises(ValidationError):
        action.risk = ActionRisk.HIGH_RISK


def test_approval_required_no_longer_exists() -> None:
    assert "approval_required" not in ActionDefinition.model_fields
    for value in (True, False):
        with pytest.raises(ValidationError):
            ActionDefinition(**VALID, approval_required=value)


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
        {"description": "  "},
        {"handler": "module.function"},
        {"tool": "some_tool"},
    ],
)
def test_definition_rejects_invalid_values(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        ActionDefinition(**{**VALID, **overrides})


def test_catalog_lookup_is_exact_and_immutable() -> None:
    assert CATALOG.get("orders.read").risk is ActionRisk.READ
    for near_miss in ("orders.READ", "orders", "orders.read ", "orders.*", "*"):
        assert CATALOG.get(near_miss) is None
    assert "orders.cancel" in CATALOG
    assert len(CATALOG) == 6
    with pytest.raises(TypeError):
        CATALOG._definitions["x.y"] = CATALOG.get("orders.read")  # type: ignore[index]


def test_catalog_rejects_duplicates_and_non_definitions() -> None:
    action = definition("orders.read", ActionRisk.READ, ActionScopeRequirement.STORE)
    with pytest.raises(ValueError, match="duplicate"):
        ActionCatalog([action, action])
    with pytest.raises(TypeError):
        ActionCatalog([{"name": "orders.read"}])  # type: ignore[list-item]


def test_intent_carries_only_the_name() -> None:
    assert set(ActionIntent.model_fields) == {"name"}
    intent = ActionIntent(name="orders.read")
    assert intent.model_dump() == {"name": "orders.read"}
    with pytest.raises(ValidationError):
        intent.name = "orders.refund"
    with pytest.raises(ValidationError):
        ActionIntent(action_name="orders.read")  # the old field name is not accepted


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
def test_intent_cannot_smuggle_governance_metadata(smuggled: dict) -> None:
    payload = {"name": "orders.refund", **smuggled}
    with pytest.raises(ValidationError):
        ActionIntent(**payload)
    with pytest.raises(ValidationError):
        ActionIntent.model_validate(payload)
    with pytest.raises(ValidationError):
        ActionIntent.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "  "}, {}])
def test_intent_requires_a_name(bad: dict) -> None:
    with pytest.raises(ValidationError):
        ActionIntent(**bad)


def test_scope_validation() -> None:
    assert ActionScope(company_id="c").store_id is None
    invalid = (
        {"company_id": ""},
        {"company_id": "c", "store_id": " "},
        {"company_id": "c", "x": 1},
    )
    for bad in invalid:
        with pytest.raises(ValidationError):
            ActionScope(**bad)
