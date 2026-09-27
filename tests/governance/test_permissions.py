import inspect

import pytest

from app.governance import (
    ActionRisk,
    ActionScopeRequirement,
    PermissionDecision,
    PermissionEvaluator,
    PermissionReason,
)
from app.governance import permissions as permissions_module
from tests.governance.factories import (
    CATALOG,
    STORE_A,
    STORE_B,
    actor,
    company_scope,
    definition,
    store_scope,
)

EVALUATOR = PermissionEvaluator()
STORE_READ = CATALOG.get("orders.read")
STORE_CANCEL = CATALOG.get("orders.cancel")
COMPANY_READ = CATALOG.get("reports.read")


# ----- contract ---------------------------------------------------------------------------


def test_evaluator_takes_a_trusted_definition_and_knows_no_catalog_or_intent() -> None:
    assert list(inspect.signature(PermissionEvaluator).parameters) == []
    assert list(inspect.signature(PermissionEvaluator.evaluate).parameters) == [
        "self", "actor", "action", "scope",
    ]  # fmt: skip
    source = inspect.getsource(permissions_module)
    assert "ActionCatalog" not in source and "ActionIntent" not in source
    assert "UNKNOWN_ACTION" not in {r.name for r in PermissionReason}


def test_evaluator_accepts_a_definition_built_directly() -> None:
    uncatalogued = definition("stock.read", ActionRisk.READ, ActionScopeRequirement.COMPANY)
    decision = EVALUATOR.evaluate(
        actor(permissions=frozenset({"stock.read"})), uncatalogued, company_scope()
    )
    assert decision.allowed


def test_decision_fields_and_exact_required_permission() -> None:
    assert set(PermissionDecision.model_fields) == {
        "allowed", "reason", "action_name", "required_permission",
    }  # fmt: skip
    custom = definition(
        "orders.export", ActionRisk.READ, ActionScopeRequirement.STORE,
        required_permission="orders.export_csv",
    )  # fmt: skip
    for granted in (frozenset(), frozenset({"orders.export_csv"})):
        decision = EVALUATOR.evaluate(actor(permissions=granted), custom, store_scope())
        assert decision.action_name == "orders.export"
        assert decision.required_permission == "orders.export_csv"
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen instance error
        decision.allowed = False  # type: ignore[misc]


# ----- reasons ----------------------------------------------------------------------------


def test_granted() -> None:
    assert EVALUATOR.evaluate(actor(), STORE_READ, store_scope()).reason is PermissionReason.GRANTED
    assert EVALUATOR.evaluate(actor(), COMPANY_READ, company_scope()).allowed


def test_no_actor() -> None:
    assert EVALUATOR.evaluate(None, STORE_READ, store_scope()).reason is PermissionReason.NO_ACTOR


@pytest.mark.parametrize("action", [STORE_READ, COMPANY_READ])
def test_company_must_match(action) -> None:
    scope = store_scope(company_id="company-2")
    assert EVALUATOR.evaluate(actor(), action, scope).reason is PermissionReason.COMPANY_MISMATCH


def test_missing_permission() -> None:
    reader = actor(permissions=frozenset({"orders.read"}))
    decision = EVALUATOR.evaluate(reader, STORE_CANCEL, store_scope())
    assert decision.reason is PermissionReason.MISSING_PERMISSION


@pytest.mark.parametrize(
    "granted", ["*", "orders.*", "orders", "ORDERS.CANCEL", "order.cancel", "orders.cancel.all"]
)
def test_permissions_match_exactly_without_wildcards(granted: str) -> None:
    decision = EVALUATOR.evaluate(
        actor(permissions=frozenset({granted})), STORE_CANCEL, store_scope()
    )
    assert decision.reason is PermissionReason.MISSING_PERMISSION


def test_roles_alone_grant_nothing() -> None:
    admin = actor(role_ids=frozenset({"admin", "owner", "superuser"}), permissions=frozenset())
    assert EVALUATOR.evaluate(admin, STORE_READ, store_scope()).reason is (
        PermissionReason.MISSING_PERMISSION
    )
    assert EVALUATOR.evaluate(admin, COMPANY_READ, company_scope()).reason is (
        PermissionReason.MISSING_PERMISSION
    )


@pytest.mark.parametrize("actor_type", ["user", "api_client", "system_agent"])
def test_actor_type_grants_nothing(actor_type: str) -> None:
    bare = actor(actor_type=actor_type, permissions=frozenset())
    assert not EVALUATOR.evaluate(bare, STORE_READ, store_scope()).allowed


# ----- store scope --------------------------------------------------------------------------


def test_store_action_requires_a_target_store() -> None:
    assert EVALUATOR.evaluate(actor(), STORE_READ, company_scope()).reason is (
        PermissionReason.STORE_SCOPE_MISSING
    )


def test_store_action_requires_the_exact_granted_store() -> None:
    assert EVALUATOR.evaluate(actor(), STORE_READ, store_scope(STORE_B)).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )
    assert EVALUATOR.evaluate(actor(store_ids=frozenset()), STORE_READ, store_scope()).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )
    assert EVALUATOR.evaluate(
        actor(store_ids=frozenset({"*"})), STORE_READ, store_scope()
    ).reason is (PermissionReason.STORE_NOT_PERMITTED)
    both = actor(store_ids=frozenset({STORE_A, STORE_B}))
    assert EVALUATOR.evaluate(both, STORE_READ, store_scope(STORE_B)).allowed


# ----- company scope ------------------------------------------------------------------------


def test_company_action_is_allowed_with_a_store_in_scope() -> None:
    for store in (STORE_A, STORE_B, "store-not-granted"):
        assert EVALUATOR.evaluate(actor(), COMPANY_READ, store_scope(store)).allowed


def test_company_action_ignores_actor_store_ids() -> None:
    for stores in (frozenset(), frozenset({STORE_B}), frozenset({"*"})):
        assert EVALUATOR.evaluate(actor(store_ids=stores), COMPANY_READ, company_scope()).allowed
        assert EVALUATOR.evaluate(actor(store_ids=stores), COMPANY_READ, store_scope()).allowed


# ----- evaluation order ---------------------------------------------------------------------


def test_check_order() -> None:
    no_rights = actor(permissions=frozenset(), store_ids=frozenset())
    # 1. no actor beats everything
    assert EVALUATOR.evaluate(None, STORE_READ, store_scope(company_id="x")).reason is (
        PermissionReason.NO_ACTOR
    )
    # 2. company mismatch beats missing permission and store checks
    assert EVALUATOR.evaluate(no_rights, STORE_READ, store_scope(company_id="x")).reason is (
        PermissionReason.COMPANY_MISMATCH
    )
    # 3. missing permission beats both store checks
    assert EVALUATOR.evaluate(no_rights, STORE_READ, company_scope()).reason is (
        PermissionReason.MISSING_PERMISSION
    )
    assert EVALUATOR.evaluate(no_rights, STORE_READ, store_scope(STORE_B)).reason is (
        PermissionReason.MISSING_PERMISSION
    )
    # 4a. missing store beats store membership
    assert EVALUATOR.evaluate(actor(store_ids=frozenset()), STORE_READ, company_scope()).reason is (
        PermissionReason.STORE_SCOPE_MISSING
    )
    # 4b. then store membership
    assert EVALUATOR.evaluate(actor(), STORE_READ, store_scope(STORE_B)).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )
