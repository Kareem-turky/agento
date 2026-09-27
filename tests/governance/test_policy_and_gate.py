import pytest

from app.governance import (
    ActionIntent,
    ActionRisk,
    ActionScopeRequirement,
    BaselinePolicyEvaluator,
    GovernanceGate,
    PermissionDecision,
    PermissionEvaluator,
    PermissionReason,
    PolicyDecision,
    PolicyOutcome,
    PolicyReason,
)
from tests.governance.factories import (
    CATALOG,
    STORE_B,
    actor,
    company_scope,
    definition,
    store_scope,
)

GATE = GovernanceGate(CATALOG)
POLICY = BaselinePolicyEvaluator()


def decide(actor_ctx, name: str, scope=None) -> PolicyDecision:
    return GATE.decide(actor_ctx, ActionIntent(name=name), scope or store_scope())


def permission(action, allowed: bool) -> PermissionDecision:
    return PermissionDecision(
        allowed=allowed,
        reason=PermissionReason.GRANTED if allowed else PermissionReason.MISSING_PERMISSION,
        action_name=action.name,
        required_permission=action.required_permission,
    )


# ----- baseline policy (evaluator on its own) -------------------------------------------

BASELINE = [
    (ActionRisk.READ, PolicyOutcome.ALLOW, PolicyReason.READ_ALLOWED),
    (ActionRisk.LOW_RISK_WRITE, PolicyOutcome.ALLOW, PolicyReason.LOW_RISK_WRITE_ALLOWED),
    (ActionRisk.MEDIUM_RISK, PolicyOutcome.REQUIRE_APPROVAL,
     PolicyReason.MEDIUM_RISK_REQUIRES_APPROVAL),
    (ActionRisk.HIGH_RISK, PolicyOutcome.REQUIRE_APPROVAL,
     PolicyReason.HIGH_RISK_REQUIRES_APPROVAL),
]  # fmt: skip


@pytest.mark.parametrize(("risk", "outcome", "reason"), BASELINE)
@pytest.mark.parametrize("scope", list(ActionScopeRequirement))
def test_permitted_action_follows_the_baseline(risk, outcome, reason, scope) -> None:
    action = definition("x.act", risk, scope)
    decision = POLICY.evaluate(action, permission(action, allowed=True))
    assert (decision.outcome, decision.reason, decision.risk) == (outcome, reason, risk)
    assert decision.permission.allowed


@pytest.mark.parametrize("risk", list(ActionRisk))
def test_denied_permission_is_always_deny(risk) -> None:
    action = definition("x.act", risk, ActionScopeRequirement.STORE)
    decision = POLICY.evaluate(action, permission(action, allowed=False))
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason is PolicyReason.PERMISSION_DENIED
    assert decision.risk is risk


def test_policy_reasons_have_no_approval_override() -> None:
    assert {r.value for r in PolicyReason} == {
        "unknown_action", "permission_denied", "read_allowed", "low_risk_write_allowed",
        "medium_risk_requires_approval", "high_risk_requires_approval",
    }  # fmt: skip


def test_policy_rejects_a_permission_decision_for_another_action() -> None:
    read, cancel = CATALOG.get("orders.read"), CATALOG.get("orders.cancel")
    with pytest.raises(ValueError):
        POLICY.evaluate(cancel, permission(read, allowed=True))


# ----- gate ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "outcome", "reason"),
    [
        ("orders.read", PolicyOutcome.ALLOW, PolicyReason.READ_ALLOWED),
        ("orders.add_note", PolicyOutcome.ALLOW, PolicyReason.LOW_RISK_WRITE_ALLOWED),
        ("orders.cancel", PolicyOutcome.REQUIRE_APPROVAL,
         PolicyReason.MEDIUM_RISK_REQUIRES_APPROVAL),
        ("orders.refund", PolicyOutcome.REQUIRE_APPROVAL,
         PolicyReason.HIGH_RISK_REQUIRES_APPROVAL),
    ],
)  # fmt: skip
def test_gate_applies_baseline_for_permitted_actor(name, outcome, reason) -> None:
    decision = decide(actor(), name)
    assert (decision.outcome, decision.reason) == (outcome, reason)
    assert decision.permission.required_permission == name


def test_gate_company_actions() -> None:
    assert decide(actor(), "reports.read", company_scope()).outcome is PolicyOutcome.ALLOW
    assert decide(actor(), "reports.read", store_scope(STORE_B)).outcome is PolicyOutcome.ALLOW
    assert decide(actor(), "settings.update", company_scope()).outcome is (
        PolicyOutcome.REQUIRE_APPROVAL
    )


@pytest.mark.parametrize("name", ["orders.cancel", "orders.refund", "settings.update"])
def test_unauthorized_medium_and_high_risk_stay_deny(name: str) -> None:
    scope = company_scope() if name == "settings.update" else store_scope()
    cases = [
        None,
        actor(permissions=frozenset()),
        actor(role_ids=frozenset({"admin"}), permissions=frozenset()),
        actor(company_id="company-2"),
    ]
    for actor_ctx in cases:
        decision = decide(actor_ctx, name, scope)
        assert decision.outcome is PolicyOutcome.DENY, (name, actor_ctx)
        assert decision.reason is PolicyReason.PERMISSION_DENIED


def test_store_restrictions_deny_through_the_gate() -> None:
    assert decide(actor(), "orders.cancel", store_scope(STORE_B)).outcome is PolicyOutcome.DENY
    assert decide(actor(store_ids=frozenset()), "orders.read").outcome is PolicyOutcome.DENY
    assert decide(actor(), "orders.read", company_scope()).outcome is PolicyOutcome.DENY


@pytest.mark.parametrize("name", ["orders.delete", "ORDERS.READ", "orders", "*", "orders.*"])
def test_unknown_action_is_denied_by_the_gate(name: str) -> None:
    decision = decide(actor(permissions=frozenset({"*", name})), name)
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason is PolicyReason.UNKNOWN_ACTION
    assert decision.action_name == name
    assert decision.risk is None and decision.permission is None


class _RecordingEvaluator(PermissionEvaluator):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate(self, actor, action, scope):
        self.calls.append(action.name)
        return super().evaluate(actor, action, scope)


def test_unknown_action_never_reaches_the_permission_evaluator() -> None:
    recorder = _RecordingEvaluator()
    gate = GovernanceGate(CATALOG, permissions=recorder)
    gate.decide(actor(), ActionIntent(name="orders.delete"), store_scope())
    assert recorder.calls == []
    gate.decide(actor(), ActionIntent(name="orders.read"), store_scope())
    assert recorder.calls == ["orders.read"]


def test_risk_and_permission_come_from_the_catalog() -> None:
    intent = ActionIntent.model_validate({"name": "orders.refund"})
    decision = GATE.decide(actor(), intent, store_scope())
    assert decision.risk is ActionRisk.HIGH_RISK
    assert decision.permission.required_permission == "orders.refund"


def test_decisions_are_immutable_and_serializable() -> None:
    decision = decide(actor(), "orders.cancel")
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen instance error
        decision.outcome = PolicyOutcome.ALLOW  # type: ignore[misc]
    dumped = decision.model_dump(mode="json")
    assert dumped["outcome"] == "require_approval"
    assert dumped["permission"] == {
        "allowed": True, "reason": "granted", "action_name": "orders.cancel",
        "required_permission": "orders.cancel",
    }  # fmt: skip
    unknown = decide(actor(), "orders.delete").model_dump(mode="json")
    assert unknown["permission"] is None and unknown["risk"] is None
