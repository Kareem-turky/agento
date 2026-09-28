"""First governed business write, end to end through REAL product components.

RequestContext + ActionIntent + ActionScope + raw parameters -> GovernanceGate ->
ExecutionCoordinator -> CreateOperationalTicketHandler -> MockTicketingAdapter ->
mock ticket desk -> independent re-read -> audit -> ActionRun.
Only the audit sink is a test double.
"""

import json
from uuid import UUID

import pytest

from app.commerce.domain import ExternalReference, TicketStatus
from app.context.models import RequestContext
from app.execution import ActionRunReason, ActionRunStatus, AuditEventType
from app.governance import ActionScope, PermissionReason, PolicyOutcome, PolicyReason
from app.integrations.commerce.mock import MOCK_SYSTEM_ID, EntityType, MockTicketWriteMode
from app.integrations.commerce.mock import canonical_id as mock_canonical_id
from tests.execution.fakes import RecordingAuditSink, run
from tests.operations.helpers import (
    ACTION,
    COMPANY,
    OTHER_COMPANY,
    OTHER_STORE,
    PARAMS,
    STORE,
    actor,
    request,
    stack,
)

S, R, E = ActionRunStatus, ActionRunReason, AuditEventType


def test_happy_path_creates_and_verifies_a_real_ticket() -> None:
    s = stack()
    result = s.run()

    assert result.policy_decision.outcome is PolicyOutcome.ALLOW
    assert (result.status, result.reason, result.audit_complete) == (S.VERIFIED, R.VERIFIED, True)
    assert result.verification_result.reason_code == "ticket_present"
    assert s.sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.EXECUTION_STARTED,
                            E.EXECUTION_COMPLETED, E.VERIFICATION_STARTED, E.VERIFIED]  # fmt: skip

    # Independently fetch the ticket from the integration.
    ticket = run(s.adapter.get_ticket(UUID(result.execution_result.reference_id)))
    assert str(ticket.id) == result.execution_result.reference_id
    assert (str(ticket.company_id), str(ticket.store_id)) == (COMPANY, STORE)
    assert (ticket.title, ticket.description) == (PARAMS["title"], PARAMS["description"])
    assert ticket.status is TicketStatus.OPEN
    provider_key = f"tkt_{result.run_id}"
    assert ticket.external_refs == frozenset(
        {ExternalReference(system=MOCK_SYSTEM_ID, external_id=provider_key)}
    )
    assert ticket.id == mock_canonical_id(EntityType.TICKET, provider_key) != provider_key
    assert run(s.adapter.find_ticket_by_correlation(result.run_id)) == ticket
    assert s.desk.ticket_count == 1

    # The write used only trusted scope, with the run id as correlation.
    (call,) = s.spy.creates
    assert call["company_id"] == UUID(COMPANY) and call["store_id"] == UUID(STORE)
    assert call["correlation_id"] == result.run_id
    # Verification was an independent correlation re-read.
    assert s.spy.correlation_reads == [result.run_id]


def test_the_ticket_uses_the_mock_commerce_store_universe() -> None:
    from app.integrations.commerce.mock import MockCommerceAdapter

    s = stack()
    result = s.run()
    ticket = run(s.adapter.get_ticket(UUID(result.execution_result.reference_id)))
    store = run(MockCommerceAdapter().get_store(ticket.store_id))
    assert store.company_id == ticket.company_id


@pytest.mark.parametrize(
    ("req", "scope", "reason"),
    [
        (RequestContext(), None, PermissionReason.NO_ACTOR),
        (request(actor(permissions=frozenset({"orders.read"}))), None,
         PermissionReason.MISSING_PERMISSION),
        (request(actor(role_ids=frozenset({"admin"}), permissions=frozenset())), None,
         PermissionReason.MISSING_PERMISSION),
        (request(actor(company_id=OTHER_COMPANY)), None, PermissionReason.COMPANY_MISMATCH),
        (request(), ActionScope(company_id=COMPANY, store_id=OTHER_STORE),
         PermissionReason.STORE_NOT_PERMITTED),
        (request(actor(store_ids=frozenset())), None, PermissionReason.STORE_NOT_PERMITTED),
        (request(), ActionScope(company_id=COMPANY), PermissionReason.STORE_SCOPE_MISSING),
        (request(actor(permissions=frozenset({"*"}))), None, PermissionReason.MISSING_PERMISSION),
        (request(actor(permissions=frozenset({"tickets.*"}))), None,
         PermissionReason.MISSING_PERMISSION),
        (request(actor(permissions=frozenset({"tickets"}))), None,
         PermissionReason.MISSING_PERMISSION),
    ],
)  # fmt: skip
def test_governance_denials_never_write(req, scope, reason) -> None:
    s = stack()
    result = s.run(req=req, scope=scope)
    assert result.status is S.DENIED
    assert result.policy_decision.reason is PolicyReason.PERMISSION_DENIED
    assert result.policy_decision.permission.reason is reason
    assert s.desk.ticket_count == 0 and s.spy.creates == []
    assert s.spy.correlation_reads == []


def test_exact_action_name_only() -> None:
    s = stack()
    registry_handler = s.handler
    assert registry_handler.action_name == ACTION
    for other in ("operations.ticket", "operations.ticket.update", "Operations.Ticket.Create"):
        result = s.run(action=other)
        assert result.status is S.DENIED
        assert result.policy_decision.reason is PolicyReason.UNKNOWN_ACTION
    assert s.desk.ticket_count == 0 and s.spy.creates == []


SMUGGLED = {
    "company_id": OTHER_COMPANY, "store_id": OTHER_STORE, "actor_id": "admin",
    "permissions": ["*"], "role_ids": ["admin"], "risk": "read",
    "required_permission": "notes.read", "approval_required": False,
    "run_id": str(UUID(int=1)), "correlation_id": str(UUID(int=2)),
}  # fmt: skip


@pytest.mark.parametrize("field", sorted(SMUGGLED))
def test_scope_and_identity_cannot_be_injected_through_parameters(field: str) -> None:
    s = stack()
    result = s.run({**PARAMS, field: SMUGGLED[field]})
    assert (result.status, result.reason) == (S.FAILED, R.INPUT_INVALID)
    assert s.desk.ticket_count == 0 and s.spy.creates == []
    assert s.spy.correlation_reads == []


@pytest.mark.parametrize(
    "params",
    [
        {"title": "", "description": "d"},
        {"title": "   ", "description": "d"},
        {"title": "t" * 161, "description": "d"},
        {"title": "t", "description": ""},
        {"title": "t", "description": "d" * 4001},
        {"title": "t", "description": "d", "priority": "high"},
    ],
)
def test_invalid_input_never_writes_or_verifies(params) -> None:
    s = stack()
    result = s.run(params)
    assert (result.status, result.reason) == (S.FAILED, R.INPUT_INVALID)
    assert s.desk.ticket_count == 0 and s.spy.creates == []
    assert s.spy.correlation_reads == []
    assert E.EXECUTION_STARTED not in s.sink.types


def test_confirmed_no_effect_write_fails_without_verification() -> None:
    s = stack(MockTicketWriteMode.CONFIRMED_NO_EFFECT)
    result = s.run()
    assert (result.status, result.reason) == (S.FAILED, R.EXECUTION_FAILED_NO_EFFECT)
    assert s.desk.ticket_count == 0
    assert s.spy.correlation_reads == []
    assert result.verification_result is None
    assert E.VERIFICATION_STARTED not in s.sink.types


def test_uncertain_after_write_verifies_the_real_ticket_but_needs_a_human() -> None:
    s = stack(MockTicketWriteMode.UNCERTAIN_AFTER_WRITE)
    result = s.run()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert result.status is not S.VERIFIED
    assert result.execution_result is None
    assert result.verification_result.verified is True
    assert result.verification_result.reason_code == "ticket_present"
    assert result.audit_complete is True
    # The ticket physically exists, exactly once for this correlation.
    assert s.desk.ticket_count == 1
    ticket = run(s.adapter.find_ticket_by_correlation(result.run_id))
    assert ticket is not None and str(ticket.store_id) == STORE
    assert s.spy.correlation_reads == [result.run_id]
    assert s.sink.types[-4:] == [E.EXECUTION_STARTED, E.EXECUTION_FAILED,
                                 E.VERIFICATION_STARTED, E.REQUIRES_HUMAN]  # fmt: skip
    assert s.sink.events[-1].verification_code == "ticket_present"


@pytest.mark.parametrize("failing", [E.EXECUTION_FAILED, E.VERIFICATION_STARTED])
def test_uncertain_after_write_with_audit_failure_still_rereads(failing) -> None:
    s = stack(
        MockTicketWriteMode.UNCERTAIN_AFTER_WRITE,
        sink=RecordingAuditSink(fail_on=frozenset({failing})),
    )
    result = s.run()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert result.audit_complete is False
    assert s.spy.correlation_reads == [result.run_id]
    assert result.verification_result.verified is True
    assert s.desk.ticket_count == 1


def test_verification_mismatch_requires_a_human() -> None:
    s = stack(MockTicketWriteMode.ALTERED_ON_WRITE)
    result = s.run()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.VERIFICATION_FAILED)
    assert result.verification_result.reason_code == "ticket_mismatch"
    assert result.execution_result is not None
    assert result.status is not S.VERIFIED


def test_post_execution_audit_failure_never_verifies() -> None:
    s = stack(sink=RecordingAuditSink(fail_on=frozenset({E.EXECUTION_COMPLETED})))
    result = s.run()
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.AUDIT_INCOMPLETE)
    assert result.verification_result.verified is True


TITLE_MARKER = "TITLE-MARKER-6f1c"
BODY_MARKER = "BODY-MARKER-9a2e"


@pytest.mark.parametrize("mode", list(MockTicketWriteMode))
def test_audit_holds_no_ticket_content_or_provider_data(mode) -> None:
    s = stack(mode)
    result = s.run({"title": f"t {TITLE_MARKER}", "description": f"d {BODY_MARKER}"})
    audit = json.dumps([e.model_dump(mode="json") for e in s.sink.events])
    run_dump = result.model_dump_json()
    for text in (audit, run_dump):
        assert TITLE_MARKER not in text and BODY_MARKER not in text
        assert "tkt_" not in text and "acct_demo" not in text and "shop_north" not in text
        assert "desk" not in text and "timed out" not in text


def test_repeated_runs_create_separate_tickets() -> None:
    s = stack()
    first, second = s.run(), s.run()
    assert first.status is second.status is S.VERIFIED
    assert first.execution_result.reference_id != second.execution_result.reference_id
    assert s.desk.ticket_count == 2
