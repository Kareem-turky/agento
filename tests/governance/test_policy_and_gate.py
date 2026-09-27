import pytest

from app.governance import (
    ActionIntent,
    ActionRisk,
    BaselinePolicyEvaluator,
    GovernanceGate,
    PermissionDecision,
    PermissionReason,
    PolicyOutcome,
    PolicyReason,
)
from tests.governance.factories import CATALOG, STORE_B, actor, company_scope, store_scope

GATE = GovernanceGate(CATALOG)


def decide(actor_ctx, name: str, scope=None):
    return GATE.decide(actor_ctx, ActionIntent(action_name=name), scope or store_scope())


@pytest.mark.parametrize(
    ("name", "outcome", "reason", "risk"),
    [
        ("orders.read", PolicyOutcome.ALLOW, PolicyReason.READ_ALLOWED, ActionRisk.READ),
        ("orders.add_note", PolicyOutcome.ALLOW, PolicyReason.LOW_RISK_WRITE_ALLOWED,
         ActionRisk.LOW_RISK_WRITE),
        ("orders.cancel", PolicyOutcome.REQUIRE_APPROVAL,
         PolicyReason.MEDIUM_RISK_REQUIRES_APPROVAL, ActionRisk.MEDIUM_RISK),
        ("orders.refund", PolicyOutcome.REQUIRE_APPROVAL,
         PolicyReason.HIGH_RISK_REQUIRES_APPROVAL, ActionRisk.HIGH_RISK),
        ("orders.tag", PolicyOutcome.REQUIRE_APPROVAL, PolicyReason.ACTION_REQUIRES_APPROVAL,
         ActionRisk.LOW_RISK_WRITE),
    ],
)  # fmt: skip
def test_baseline_policy_for_permitted_actor(name, outcome, reason, risk) -> None:
    decision = decide(actor(), name)
    assert (decision.outcome, decision.reason, decision.risk) == (outcome, reason, risk)
    assert decision.permission.allowed
    assert decision.action_name == name


def test_company_scoped_high_risk_requires_approval() -> None:
    decision = decide(actor(), "settings.update", company_scope())
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


@pytest.mark.parametrize("name", ["orders.read", "orders.add_note", "orders.cancel",
                                  "orders.refund", "orders.tag"])  # fmt: skip
def test_unauthorized_actor_is_denied_never_sent_to_approval(name: str) -> None:
    cases = [
        (None, store_scope()),
        (actor(permissions=frozenset()), store_scope()),
        (actor(role_ids=frozenset({"admin"}), permissions=frozenset()), store_scope()),
        (actor(), store_scope(STORE_B)),
        (actor(store_ids=frozenset()), store_scope()),
        (actor(company_id="company-2"), store_scope()),
        (actor(), company_scope()),
    ]
    for actor_ctx, scope in cases:
        decision = decide(actor_ctx, name, scope)
        assert decision.outcome is PolicyOutcome.DENY, (name, actor_ctx, scope)
        assert decision.reason is PolicyReason.PERMISSION_DENIED
        assert not decision.permission.allowed


def test_unknown_action_fails_closed() -> None:
    decision = decide(actor(permissions=frozenset({"*", "orders.delete"})), "orders.delete")
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.permission.reason is PermissionReason.UNKNOWN_ACTION
    assert decision.risk is None


def test_risk_comes_from_the_catalog_not_the_caller() -> None:
    # The only thing a caller controls is the name; a high-risk action stays high risk.
    intent = ActionIntent.model_validate({"action_name": "orders.refund"})
    decision = GATE.decide(actor(), intent, store_scope())
    assert decision.risk is ActionRisk.HIGH_RISK
    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL


def test_policy_denies_an_allowed_decision_without_a_definition() -> None:
    # Defensive: a malformed "allowed" decision without a trusted definition is denied.
    malformed = PermissionDecision(allowed=True, reason=PermissionReason.GRANTED,
                                   action_name="orders.read", action=None)  # fmt: skip
    decision = BaselinePolicyEvaluator().evaluate(malformed)
    assert decision.outcome is PolicyOutcome.DENY


def test_decisions_are_immutable_and_serializable() -> None:
    decision = decide(actor(), "orders.cancel")
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen instance error
        decision.outcome = PolicyOutcome.ALLOW  # type: ignore[misc]
    dumped = decision.model_dump(mode="json")
    assert dumped["outcome"] == "require_approval"
    assert dumped["permission"]["reason"] == "granted"
    assert dumped["permission"]["action"]["required_permission"] == "orders.cancel"


def test_every_risk_has_a_baseline_outcome() -> None:
    assert {r for r in ActionRisk} == {decide(actor(), n).risk for n in
                                       ("orders.read", "orders.add_note", "orders.cancel",
                                        "orders.refund")}  # fmt: skip
