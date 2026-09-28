"""Security and lifecycle matrix for the ExecutionCoordinator."""

import itertools
import json
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.execution import (
    ActionExecutionContext,
    ActionRunReason,
    ActionRunStatus,
    AuditEventType,
    ExecutionResult,
)
from app.governance import PolicyOutcome, PolicyReason
from tests.execution.fakes import (
    COMPANY,
    SECRET_MARKER,
    STORE,
    VALID_PARAMS,
    FakeHandler,
    NoteInput,
    RecordingAuditSink,
    actor,
    coordinator,
    execute,
    request,
    store_scope,
)

S, R, E = ActionRunStatus, ActionRunReason, AuditEventType


def handlers() -> dict[str, FakeHandler]:
    names = ("notes.add", "notes.read", "orders.cancel", "orders.refund", "reports.rebuild")
    return {name: FakeHandler(name) for name in names}


def assert_untouched(handler: FakeHandler) -> None:
    assert handler.validate_calls == []
    assert handler.execute_calls == []
    assert handler.verify_calls == []


# ----- governance first -----------------------------------------------------------------


@pytest.mark.parametrize(
    "req",
    [
        request(actor(permissions=frozenset())),
        request(actor(role_ids=frozenset({"admin", "owner"}), permissions=frozenset())),
        request(actor(company_id="company-2")),
        request(actor(store_ids=frozenset())),
    ],
)
def test_denied_never_touches_the_handler(req) -> None:
    hs = handlers()
    coord, sink = coordinator(*hs.values())
    result = execute(coord, "notes.add", req=req)
    assert (result.status, result.reason) == (S.DENIED, R.POLICY_DENIED)
    assert result.policy_decision.outcome is PolicyOutcome.DENY
    assert_untouched(hs["notes.add"])
    assert sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.DENIED]


def test_no_actor_is_denied() -> None:
    hs = handlers()
    coord, _ = coordinator(*hs.values())
    from app.context.models import RequestContext

    result = execute(coord, req=RequestContext())
    assert result.status is S.DENIED
    assert_untouched(hs["notes.add"])


@pytest.mark.parametrize("name", ["orders.cancel", "orders.refund"])
def test_require_approval_never_executes(name: str) -> None:
    hs = handlers()
    coord, sink = coordinator(*hs.values())
    result = execute(coord, name)
    assert (result.status, result.reason) == (S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED)
    assert result.policy_decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert result.execution_result is None and result.verification_result is None
    assert_untouched(hs[name])
    assert sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.AWAITING_APPROVAL]


def test_no_approval_bypass_exists() -> None:
    hs = handlers()
    coord, _ = coordinator(*hs.values())
    for params in ({"approved": True}, {"approval_token": "x"}, {"status": "approved"}):
        assert execute(coord, "orders.refund", params=params).status is S.AWAITING_APPROVAL
    import inspect

    assert list(inspect.signature(coord.run).parameters) == ["request", "intent", "scope",
                                                            "parameters"]  # fmt: skip
    assert_untouched(hs["orders.refund"])


def test_unknown_action_never_touches_any_handler() -> None:
    hs = handlers()
    coord, sink = coordinator(*hs.values())
    result = execute(coord, "notes.delete")
    assert (result.status, result.reason) == (S.DENIED, R.POLICY_DENIED)
    assert result.policy_decision.reason is PolicyReason.UNKNOWN_ACTION
    for handler in hs.values():
        assert_untouched(handler)


# ----- handler lookup and validation ------------------------------------------------------


def test_missing_handler_fails_closed() -> None:
    coord, sink = coordinator(*handlers().values())
    result = execute(coord, "labels.print")
    assert (result.status, result.reason) == (S.FAILED, R.HANDLER_NOT_REGISTERED)
    assert result.policy_decision.outcome is PolicyOutcome.ALLOW
    assert sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.HANDLER_NOT_REGISTERED]


def test_exact_governed_name_selects_the_handler() -> None:
    hs = handlers()
    coord, _ = coordinator(*hs.values())
    assert execute(coord, "notes.add").status is S.VERIFIED
    assert len(hs["notes.add"].execute_calls) == 1
    assert all(not h.execute_calls for n, h in hs.items() if n != "notes.add")


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"order_ref": "ord-1"},
        {"order_ref": "", "text": "x"},
        {"order_ref": "ord-1", "text": "x", "extra": SECRET_MARKER},
        {"order_ref": "ord-1", "text": "x" * 500},
        {"order_ref": 5, "text": ["x"]},
    ],
)
def test_invalid_input_fails_without_execute_or_verify(params) -> None:
    handler = FakeHandler()
    coord, sink = coordinator(handler)
    result = execute(coord, params=params)
    assert (result.status, result.reason) == (S.FAILED, R.INPUT_INVALID)
    assert len(handler.validate_calls) == 1
    assert handler.execute_calls == [] and handler.verify_calls == []
    assert sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.VALIDATION_FAILED]
    assert SECRET_MARKER not in result.model_dump_json()


def test_non_mapping_parameters_are_invalid() -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler)
    result = execute(coord, params=["not", "a", "mapping"])  # type: ignore[arg-type]
    assert result.reason is R.INPUT_INVALID
    assert handler.execute_calls == []


@pytest.mark.parametrize("bad", [{"order_ref": "x", "text": "y"}, "raw", True])
def test_validator_must_return_an_immutable_model(bad) -> None:
    handler = FakeHandler(validate_returns=bad)
    coord, _ = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.FAILED, R.HANDLER_CONTRACT_VIOLATION)
    assert handler.execute_calls == []


def test_executor_receives_validated_model_not_raw_parameters() -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler)
    params = dict(VALID_PARAMS)
    execute(coord, params=params)
    (received,) = handler.execute_calls
    assert isinstance(received, NoteInput)
    assert received is not params and not isinstance(received, dict)
    assert received == NoteInput(**VALID_PARAMS)
    raw_seen = handler.validate_calls[0]
    assert raw_seen is not params  # a read-only copy, not the caller's object
    with pytest.raises(TypeError):
        raw_seen["text"] = "changed"  # type: ignore[index]


# ----- trusted execution context ---------------------------------------------------------


def test_one_trusted_context_is_passed_to_execute_and_verify() -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler, ids=lambda: UUID(int=7))
    req = request(actor(actor_id="user-7"), channel="web", session_id="sess-1")
    result = execute(coord, req=req)
    (exec_ctx,) = handler.execute_contexts
    (verify_ctx,) = handler.verify_contexts
    assert exec_ctx is verify_ctx  # the same immutable object, built once
    assert exec_ctx == ActionExecutionContext(
        run_id=result.run_id, request_id=req.request_id, action_name="notes.add",
        actor_id="user-7", actor_type="user", company_id=COMPANY, store_id=STORE,
        channel="web", session_id="sess-1",
    )  # fmt: skip
    with pytest.raises(ValidationError):
        exec_ctx.company_id = "company-2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ActionExecutionContext(**{**exec_ctx.model_dump(), "extra": "x"})


@pytest.mark.parametrize("behaviour", ["uncertain", "crash", "bad_result"])
def test_uncertain_verification_gets_the_same_context(behaviour: str) -> None:
    handler = FakeHandler(execute_behaviour=behaviour)
    coord, _ = coordinator(handler)
    execute(coord)
    assert handler.execute_contexts[0] is handler.verify_contexts[0]


SMUGGLED_CONTEXT = {
    "actor_id": "admin-1", "actor_type": "system_agent", "company_id": "company-2",
    "store_id": "store-z", "permissions": ["*"], "role_ids": ["admin"],
    "run_id": str(UUID(int=99)), "request_id": str(UUID(int=98)), "channel": "system",
    "session_id": "evil", "action_name": "orders.refund",
}  # fmt: skip


class _PermissiveInput(BaseModel):
    """A handler input that tolerates unknown keys, to prove the context is unaffected
    even when a handler's validator does not reject smuggled fields."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    order_ref: str
    text: str


def test_raw_parameters_cannot_alter_the_execution_context() -> None:
    handler = FakeHandler(validate_model=_PermissiveInput)
    coord, _ = coordinator(handler)
    result = execute(coord, params={**VALID_PARAMS, **SMUGGLED_CONTEXT})
    assert result.status is S.VERIFIED
    ctx = handler.execute_contexts[0]
    assert ctx.run_id == result.run_id
    assert ctx.request_id == UUID("00000000-0000-4000-8000-00000000000a")
    assert (ctx.actor_id, ctx.actor_type) == ("user-1", "user")
    assert (ctx.company_id, ctx.store_id) == (COMPANY, STORE)
    assert (ctx.action_name, ctx.channel, ctx.session_id) == ("notes.add", "api", None)
    assert handler.verify_contexts[0] is ctx


def test_no_context_is_built_when_nothing_executes() -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler)
    execute(coord, params={})
    execute(coord, "orders.cancel")
    assert handler.execute_contexts == [] and handler.verify_contexts == []


# ----- raw parameters cannot influence governance ---------------------------------------

SMUGGLED = {
    "actor_id": "admin-1", "company_id": "company-2", "store_id": "store-z",
    "store_ids": ["store-z"], "permissions": ["notes.add", "orders.refund"],
    "role_ids": ["admin"], "risk": "read", "required_permission": "notes.read",
    "scope_requirement": "company", "handler": "orders.refund", "approved": True,
}  # fmt: skip


def test_parameters_cannot_grant_identity_or_permissions() -> None:
    hs = handlers()
    coord, sink = coordinator(*hs.values())
    unprivileged = request(actor(actor_id="user-9", permissions=frozenset()))
    result = execute(coord, params={**VALID_PARAMS, **SMUGGLED}, req=unprivileged)
    assert result.status is S.DENIED
    assert_untouched(hs["notes.add"])
    assert {e.actor_id for e in sink.events} == {"user-9"}


def test_parameters_cannot_change_scope() -> None:
    hs = handlers()
    coord, sink = coordinator(*hs.values())
    result = execute(coord, params={**VALID_PARAMS, **SMUGGLED}, scope=store_scope("store-z"))
    assert result.status is S.DENIED  # the trusted scope decides, not parameters
    assert {(e.company_id, e.store_id) for e in sink.events} == {(COMPANY, "store-z")}


def test_parameters_cannot_lower_risk_or_change_permission() -> None:
    hs = handlers()
    coord, _ = coordinator(*hs.values())
    result = execute(coord, "orders.refund", params={**VALID_PARAMS, **SMUGGLED})
    assert result.status is S.AWAITING_APPROVAL
    assert result.policy_decision.risk.value == "high_risk"
    assert result.policy_decision.permission.required_permission == "orders.refund"
    reader = request(actor(permissions=frozenset({"notes.read"})))
    assert execute(coord, "notes.add", params=SMUGGLED, req=reader).status is S.DENIED


def test_task_008_store_and_company_rules_still_apply() -> None:
    hs = handlers()
    coord, _ = coordinator(*hs.values())
    assert execute(coord, scope=store_scope("store-b")).status is S.DENIED
    assert execute(coord, scope=store_scope(company_id="company-2")).status is S.DENIED
    from app.governance import ActionScope

    company_scope = ActionScope(company_id=COMPANY, store_id="any-store")
    assert execute(coord, "reports.rebuild", scope=company_scope).status is S.VERIFIED


# ----- execution and verification ---------------------------------------------------------


def test_success_path_is_verified_with_full_audit() -> None:
    handler = FakeHandler()
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.VERIFIED, R.VERIFIED)
    assert result.audit_complete is True
    assert result.execution_result.reference_id == "note-123"
    assert result.verification_result.verified is True
    assert len(handler.execute_calls) == 1 and len(handler.verify_calls) == 1
    assert sink.types == [E.REQUESTED, E.POLICY_DECIDED, E.EXECUTION_STARTED,
                          E.EXECUTION_COMPLETED, E.VERIFICATION_STARTED, E.VERIFIED]  # fmt: skip


def test_verification_always_follows_a_completed_execute() -> None:
    for verify_behaviour in ("ok", "mismatch", "crash", "bad_result"):
        handler = FakeHandler(verify_behaviour=verify_behaviour)
        coord, _ = coordinator(handler)
        execute(coord)
        assert len(handler.execute_calls) == 1
        assert len(handler.verify_calls) == 1
        assert handler.verify_calls[0][1].reference_id == "note-123"


def test_verification_false_requires_a_human() -> None:
    handler = FakeHandler(verify_behaviour="mismatch")
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.VERIFICATION_FAILED)
    assert result.verification_result.reason_code == "note_missing"
    assert sink.types[-1] is E.REQUIRES_HUMAN


@pytest.mark.parametrize("behaviour", ["crash", "bad_result"])
def test_verification_error_requires_a_human(behaviour: str) -> None:
    coord, _ = coordinator(FakeHandler(verify_behaviour=behaviour))
    result = execute(coord)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.VERIFICATION_ERROR)
    assert result.verification_result is None


def test_confirmed_no_effect_failure_is_failed_without_verification() -> None:
    handler = FakeHandler(execute_behaviour="no_effect")
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.FAILED, R.EXECUTION_FAILED_NO_EFFECT)
    assert len(handler.execute_calls) == 1
    assert handler.verify_calls == []  # the handler established nothing changed
    assert result.verification_result is None
    assert E.VERIFICATION_STARTED not in sink.types
    assert sink.types[-1] is E.EXECUTION_FAILED


UNCERTAIN = ["uncertain", "crash", "bad_result"]


@pytest.mark.parametrize("behaviour", UNCERTAIN)
def test_uncertain_execution_is_still_verified_without_a_receipt(behaviour: str) -> None:
    handler = FakeHandler(execute_behaviour=behaviour)
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert len(handler.execute_calls) == 1
    assert len(handler.verify_calls) == 1
    validated, receipt = handler.verify_calls[0]
    assert receipt is None  # no fabricated ExecutionResult
    assert validated == NoteInput(**VALID_PARAMS)  # validated input, never raw parameters
    assert result.execution_result is None
    assert result.audit_complete is True
    assert sink.types[-4:] == [E.EXECUTION_STARTED, E.EXECUTION_FAILED,
                               E.VERIFICATION_STARTED, E.REQUIRES_HUMAN]  # fmt: skip
    assert all(e.execution_reference_id is None for e in sink.events)


@pytest.mark.parametrize("behaviour", UNCERTAIN)
@pytest.mark.parametrize(
    ("verify_behaviour", "verified", "code"),
    [("ok", True, "note_present"), ("mismatch", False, "note_missing")],
)
def test_uncertain_execution_keeps_verification_evidence_but_is_never_verified(
    behaviour: str, verify_behaviour: str, verified: bool, code: str
) -> None:
    handler = FakeHandler(execute_behaviour=behaviour, verify_behaviour=verify_behaviour)
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert result.status is S.REQUIRES_HUMAN and result.status is not S.VERIFIED
    assert result.reason is R.EXECUTION_OUTCOME_UNCERTAIN
    assert result.verification_result.verified is verified
    assert result.verification_result.reason_code == code
    final = sink.events[-1]
    assert (final.event_type, final.run_status, final.run_reason) == (
        E.REQUIRES_HUMAN, S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN,
    )  # fmt: skip
    assert final.verification_code == code
    assert final.execution_reference_id is None


@pytest.mark.parametrize("behaviour", UNCERTAIN)
@pytest.mark.parametrize("verify_behaviour", ["crash", "bad_result"])
def test_uncertain_execution_with_broken_verifier(behaviour: str, verify_behaviour: str) -> None:
    handler = FakeHandler(execute_behaviour=behaviour, verify_behaviour=verify_behaviour)
    coord, sink = coordinator(handler)
    result = execute(coord)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert len(handler.verify_calls) == 1
    assert result.verification_result is None
    assert sink.events[-1].verification_code is None
    dumped = result.model_dump_json() + "".join(e.model_dump_json() for e in sink.events)
    assert SECRET_MARKER not in dumped and "re-read failed" not in dumped


@pytest.mark.parametrize("behaviour", UNCERTAIN)
@pytest.mark.parametrize("failing", [E.EXECUTION_FAILED, E.VERIFICATION_STARTED])
def test_audit_failure_never_skips_uncertain_verification(behaviour: str, failing) -> None:
    handler = FakeHandler(execute_behaviour=behaviour)
    sink = RecordingAuditSink(fail_on=frozenset({failing}))
    coord, _ = coordinator(handler, sink=sink)
    result = execute(coord)
    assert len(handler.verify_calls) == 1
    assert handler.verify_calls[0][1] is None
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.EXECUTION_OUTCOME_UNCERTAIN)
    assert result.audit_complete is False
    assert result.verification_result.verified is True  # evidence kept, still not VERIFIED
    assert sink.attempts[-1] is E.REQUIRES_HUMAN


def test_completed_execution_passes_its_receipt_to_verify() -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler)
    assert execute(coord).status is S.VERIFIED
    ((_, receipt),) = handler.verify_calls
    assert receipt == ExecutionResult(reference_id="note-123")


# ----- audit failures -----------------------------------------------------------------------


@pytest.mark.parametrize("failing", [E.REQUESTED, E.POLICY_DECIDED, E.EXECUTION_STARTED])
def test_pre_execution_audit_failure_blocks_execution(failing) -> None:
    handler = FakeHandler()
    coord, _ = coordinator(handler, sink=RecordingAuditSink(fail_on=frozenset({failing})))
    result = execute(coord)
    assert (result.status, result.reason) == (S.FAILED, R.AUDIT_UNAVAILABLE)
    assert result.audit_complete is False
    assert handler.execute_calls == [] and handler.verify_calls == []


@pytest.mark.parametrize("failing", [E.EXECUTION_COMPLETED, E.VERIFICATION_STARTED, E.VERIFIED])
def test_post_execution_audit_failure_still_verifies_but_never_reports_verified(failing) -> None:
    handler = FakeHandler()
    sink = RecordingAuditSink(fail_on=frozenset({failing}))
    coord, _ = coordinator(handler, sink=sink)
    result = execute(coord)
    assert len(handler.verify_calls) == 1  # verification still attempted
    assert result.status is S.REQUIRES_HUMAN
    assert result.reason is R.AUDIT_INCOMPLETE
    assert result.audit_complete is False
    assert result.verification_result.verified is True
    assert sink.attempts[-1] is E.REQUIRES_HUMAN


def test_post_execution_audit_failure_with_failed_verification() -> None:
    handler = FakeHandler(verify_behaviour="mismatch")
    coord, _ = coordinator(
        handler, sink=RecordingAuditSink(fail_on=frozenset({E.EXECUTION_COMPLETED}))
    )
    result = execute(coord)
    assert (result.status, result.reason) == (S.REQUIRES_HUMAN, R.VERIFICATION_FAILED)
    assert result.audit_complete is False


def test_terminal_audit_failure_on_deny_keeps_denied_but_marks_incomplete() -> None:
    coord, _ = coordinator(FakeHandler(), sink=RecordingAuditSink(fail_on=frozenset({E.DENIED})))
    result = execute(coord, req=request(actor(permissions=frozenset())))
    assert result.status is S.DENIED and result.audit_complete is False


# ----- audit content --------------------------------------------------------------------------


def test_audit_events_carry_trusted_metadata() -> None:
    coord, sink = coordinator(FakeHandler())
    req = request(actor(actor_id="user-7"), channel="web", session_id="sess-1")
    result = execute(coord, req=req)
    for event in sink.events:
        assert event.run_id == result.run_id
        assert event.request_id == UUID("00000000-0000-4000-8000-00000000000a")
        assert (event.actor_id, event.actor_type) == ("user-7", "user")
        assert (event.company_id, event.store_id, event.channel) == (COMPANY, STORE, "web")
        assert event.action_name == "notes.add"
    assert sink.events[0].policy_outcome is None
    assert {e.policy_outcome for e in sink.events[1:]} == {PolicyOutcome.ALLOW}
    final = sink.events[-1]
    assert (final.run_status, final.run_reason) == (S.VERIFIED, R.VERIFIED)
    assert final.execution_reference_id == "note-123"
    assert final.verification_code == "note_present"
    assert len({e.event_id for e in sink.events}) == len(sink.events)


def test_audit_never_contains_raw_parameters_results_or_error_text() -> None:
    marker_params = {"order_ref": "ord-1", "text": f"note {SECRET_MARKER}"}
    for execute_b, verify_b in itertools.product(
        ["ok", "no_effect", "uncertain", "crash", "bad_result"], ["ok", "mismatch", "crash"]
    ):
        coord, sink = coordinator(FakeHandler(execute_behaviour=execute_b,
                                              verify_behaviour=verify_b))  # fmt: skip
        result = execute(coord, params=marker_params)
        dumped = json.dumps([e.model_dump(mode="json") for e in sink.events])
        assert SECRET_MARKER not in dumped and "note " not in dumped
        assert SECRET_MARKER not in result.model_dump_json()
    bad = {"order_ref": "ord-1", "text": "x", "junk": SECRET_MARKER}
    coord, sink = coordinator(FakeHandler())
    execute(coord, params=bad)
    assert SECRET_MARKER not in json.dumps([e.model_dump(mode="json") for e in sink.events])


def test_audit_event_fields_have_no_room_for_payloads() -> None:
    from app.execution import AuditEvent

    assert set(AuditEvent.model_fields) == {
        "event_id", "run_id", "request_id", "occurred_at", "event_type", "action_name",
        "actor_id", "actor_type", "company_id", "store_id", "channel", "policy_outcome",
        "policy_reason", "run_status", "run_reason", "execution_reference_id",
        "verification_code",
    }  # fmt: skip


# ----- determinism --------------------------------------------------------------------------


def test_repeated_runs_are_deterministic_apart_from_ids() -> None:
    def outcome():
        coord, sink = coordinator(FakeHandler(verify_behaviour="mismatch"))
        result = execute(coord)
        return (result.status, result.reason, result.audit_complete,
                [(e.event_type, e.run_status, e.run_reason) for e in sink.events])  # fmt: skip

    assert outcome() == outcome()


def test_injected_ids_and_clock_are_used() -> None:
    counter = itertools.count(1)
    coord, sink = coordinator(FakeHandler(), ids=lambda: UUID(int=next(counter)))
    result = execute(coord)
    assert result.run_id == UUID(int=1)
    assert [e.event_id for e in sink.events] == [UUID(int=i) for i in range(2, 8)]
    assert {e.occurred_at.isoformat() for e in sink.events} == {"2026-03-01T12:00:00+00:00"}
