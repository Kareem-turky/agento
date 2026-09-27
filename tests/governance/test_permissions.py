import pytest

from app.governance import ActionIntent, PermissionEvaluator, PermissionReason
from tests.governance.factories import (
    CATALOG,
    COMPANY,
    STORE_A,
    STORE_B,
    actor,
    company_scope,
    store_scope,
)

EVALUATOR = PermissionEvaluator(CATALOG)


def check(actor_ctx, name: str, scope):
    return EVALUATOR.evaluate(actor_ctx, ActionIntent(action_name=name), scope)


def test_granted_store_action() -> None:
    decision = check(actor(), "orders.read", store_scope())
    assert decision.allowed and decision.reason is PermissionReason.GRANTED
    assert decision.action == CATALOG.get("orders.read")


def test_granted_company_action() -> None:
    decision = check(actor(), "reports.read", company_scope())
    assert decision.allowed and decision.reason is PermissionReason.GRANTED


def test_no_actor_is_denied() -> None:
    decision = check(None, "orders.read", store_scope())
    assert not decision.allowed and decision.reason is PermissionReason.NO_ACTOR


@pytest.mark.parametrize("name", ["orders.delete", "ORDERS.READ", "orders", "*", "orders.*"])
def test_unknown_action_is_denied(name: str) -> None:
    decision = check(actor(permissions=frozenset({name, "*"})), name, store_scope())
    assert not decision.allowed and decision.reason is PermissionReason.UNKNOWN_ACTION
    assert decision.action is None


def test_missing_permission_is_denied() -> None:
    decision = check(actor(permissions=frozenset({"orders.read"})), "orders.cancel", store_scope())
    assert decision.reason is PermissionReason.MISSING_PERMISSION


@pytest.mark.parametrize(
    "granted", ["*", "orders.*", "orders", "ORDERS.CANCEL", "order.cancel", "orders.cancel.all"]
)
def test_permissions_match_exactly_without_wildcards(granted: str) -> None:
    decision = check(actor(permissions=frozenset({granted})), "orders.cancel", store_scope())
    assert decision.reason is PermissionReason.MISSING_PERMISSION


def test_roles_alone_grant_nothing() -> None:
    admin = actor(role_ids=frozenset({"admin", "owner", "superuser"}), permissions=frozenset())
    for name, scope in [("orders.read", store_scope()), ("settings.update", company_scope())]:
        assert check(admin, name, scope).reason is PermissionReason.MISSING_PERMISSION


@pytest.mark.parametrize("actor_type", ["user", "api_client", "system_agent"])
def test_actor_type_grants_nothing(actor_type: str) -> None:
    bare = actor(actor_type=actor_type, permissions=frozenset())
    assert not check(bare, "orders.read", store_scope()).allowed


def test_store_action_requires_the_exact_target_store() -> None:
    assert check(actor(), "orders.read", store_scope(STORE_B)).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )
    assert check(actor(store_ids=frozenset()), "orders.read", store_scope()).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )
    both = actor(store_ids=frozenset({STORE_A, STORE_B}))
    assert check(both, "orders.read", store_scope(STORE_B)).allowed
    assert check(actor(store_ids=frozenset({"*"})), "orders.read", store_scope()).reason is (
        PermissionReason.STORE_NOT_PERMITTED
    )


def test_store_action_without_a_store_is_denied() -> None:
    decision = check(actor(), "orders.read", company_scope())
    assert decision.reason is PermissionReason.STORE_SCOPE_MISSING


def test_company_action_with_a_store_is_denied() -> None:
    decision = check(actor(), "reports.read", store_scope())
    assert decision.reason is PermissionReason.UNEXPECTED_STORE_SCOPE


@pytest.mark.parametrize("name,scope_factory", [("orders.read", store_scope),
                                                ("reports.read", company_scope)])  # fmt: skip
def test_company_must_match(name: str, scope_factory) -> None:
    decision = check(actor(), name, scope_factory(company_id="company-2"))
    assert decision.reason is PermissionReason.COMPANY_MISMATCH
    assert COMPANY != "company-2"


def test_company_mismatch_wins_over_store_grant() -> None:
    # Store ids are only meaningful inside the actor's own company.
    decision = check(actor(), "orders.read", store_scope(STORE_A, company_id="company-2"))
    assert decision.reason is PermissionReason.COMPANY_MISMATCH
