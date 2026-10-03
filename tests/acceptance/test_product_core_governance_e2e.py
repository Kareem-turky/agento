"""PRODUCT CORE ACCEPTANCE (Task 040), journeys G (human Approval) and H (Workflow +
Approval), including a restart between the request and the continuation.

There is deliberately NO production MEDIUM/HIGH-risk action and Task 040 adds none: the
TEST-ONLY action ``test.budget.update`` and the TEST-ONLY Workflow
``testing.approval_budget`` (tests/support) run through the REAL ProductApprovalBroker,
ExecutionCoordinator, GovernanceGate, WorkflowEngine and ApprovalService on the REAL
PostgreSQL approval, audit and Workflow repositories. Requests exist only because
governance answered REQUIRE_APPROVAL (no row is inserted by hand; there is no create
route). Decisions go through the REAL Product Approval HTTP API of the installation;
runs are inspected through the REAL Workflow HTTP API. The TEST-ONLY effect is an
in-memory counter: it does not survive a restart, the durable Product records do.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.approval_management.errors import (
    ApprovalAccessDeniedError,
    ApprovalError,
    ApprovalNotResumableError,
)
from app.context.models import ActorContext, RequestContext
from app.execution import ActionRunReason as R
from app.execution import ActionRunStatus as S
from app.workflow_management.errors import WorkflowNotResumableError
from tests.support.canonical_mock import COMPANY, SOUTH
from tests.support.product_core import (
    DECIDER,
    DECIDER_ID,
    DECIDER_PERMISSIONS,
    OPERATOR,
    REQUESTER,
    REQUESTER_ID,
    RESTRICTED,
    CoreInstallation,
    GovernedTestProcess,
    RealClock,
    requester_context,
    rows,
)

pytestmark = pytest.mark.integration

APPROVALS, APPROVAL = "/api/v1/approvals", "/api/v1/approvals/approval"
APPROVE, REJECT = f"{APPROVAL}/approve", f"{APPROVAL}/reject"
NOTE = "Approved by the core decider after review."
REJECTION = "Rejected: not this quarter."


def approval_view(client: TestClient, approval_id: UUID, headers: Any = DECIDER) -> Any:
    return client.get(APPROVAL, headers=headers, params={"approval_id": str(approval_id)})


def decide(client: TestClient, path: str, approval_id: UUID, headers: Any, note: str):
    return client.post(path, headers=headers, params={"approval_id": str(approval_id)},
                       json={"note": note})  # fmt: skip


def run_view(client: TestClient, run_id: UUID) -> dict[str, Any]:
    response = client.get("/api/v1/workflows/run", headers=OPERATOR,
                          params={"run_id": str(run_id)})  # fmt: skip
    assert response.status_code == 200
    return response.json()


def approval_row(engine: sa.Engine, approval_id: UUID) -> dict[str, Any]:
    (row,) = rows(engine, "SELECT * FROM product.approval_requests WHERE approval_id = :a",
                  a=approval_id)  # fmt: skip
    return row


def audit_for(engine: sa.Engine, approval_id: UUID) -> list[dict[str, Any]]:
    return rows(engine, "SELECT * FROM product.audit_events WHERE approval_id = :a "
                "ORDER BY recorded_at, occurred_at", a=approval_id)  # fmt: skip


# ----- journey G: a governed action waits for a human, across a restart ----------------------


def test_governed_action_needs_a_second_human_and_runs_once_across_a_restart(
    core: CoreInstallation, engine: sa.Engine, migrated: str, no_outbound_network
) -> None:
    clock = RealClock()
    process = GovernedTestProcess(migrated, clock)
    try:
        first = asyncio.run(process.request_budget())
        early = asyncio.run(process.request_budget(approval_id=first.approval_id))
        with TestClient(core.app()) as client:
            listed = client.get(APPROVALS, headers=DECIDER, params={"status": "requested"})
            view = approval_view(client, first.approval_id)
            self_decision = decide(client, APPROVE, first.approval_id, REQUESTER, NOTE)
            no_permission = decide(client, APPROVE, first.approval_id, OPERATOR, NOTE)
            hidden = approval_view(client, first.approval_id, RESTRICTED)
        # Only a human decides: a system_agent with every decision permission is refused.
        bot = RequestContext(actor=ActorContext(
            actor_id="core-automation", actor_type="system_agent", company_id=COMPANY,
            permissions=DECIDER_PERMISSIONS, store_ids=frozenset({SOUTH})),
            channel="api")  # fmt: skip
        with pytest.raises(ApprovalError):
            asyncio.run(process.world.service.approve(bot, first.approval_id, None))
        effects_before_restart = process.effects
    finally:
        asyncio.run(process.close())

    assert (first.status, first.reason) == (S.AWAITING_APPROVAL, R.APPROVAL_REQUIRED)
    assert early.reason is R.APPROVAL_NOT_DECIDED  # no continuation before a decision
    assert effects_before_restart == []
    assert str(first.approval_id) in {a["approval_id"] for a in listed.json()["approvals"]}
    safe = view.json()["approval"]
    assert (safe["action_name"], safe["risk"], safe["status"]) == (
        "test.budget.update", "medium_risk", "requested")  # fmt: skip
    assert (safe["requester_actor_id"], safe["requester_actor_type"], safe["store_id"]) == (
        REQUESTER_ID, "api_client", SOUTH)  # fmt: skip
    assert safe["source"]["kind"] == "action" and safe["consumed"] is False
    assert [c["code"] for c in safe["summary"]["changes"]] == ["budget"]
    stored = approval_row(engine, first.approval_id)
    # Exact requester binding, durably: actor id + type, company, store, action, fingerprint.
    assert (stored["company_id"], stored["store_id"], stored["action_name"]) == (
        COMPANY, SOUTH, "test.budget.update")  # fmt: skip
    assert (stored["requester_actor_id"], stored["requester_actor_type"]) == (
        REQUESTER_ID,
        "api_client",
    )
    assert len(stored["subject_fingerprint"]) == 64
    assert stored["subject_fingerprint"] not in view.text  # never exposed
    for leaked in ("subject_fingerprint", "parameters", '"amount"', '"campaign"'):
        assert leaked not in view.text, leaked
    assert (self_decision.status_code, self_decision.json()) == (
        403, {"detail": "Requesters cannot decide their own request"})  # fmt: skip
    assert no_permission.status_code == 403
    assert hidden.status_code == 403  # no approvals.read: nothing about the request leaks
    assert approval_row(engine, first.approval_id)["status"] == "requested"

    # ---- restart: a NEW installation and a NEW process on the SAME database ---------------
    process = GovernedTestProcess(migrated, clock)
    try:
        with TestClient(core.app()) as client:
            approved = decide(client, APPROVE, first.approval_id, DECIDER, NOTE)
            again = decide(client, APPROVE, first.approval_id, DECIDER, NOTE)
        wrong_actor = asyncio.run(
            process.request_budget(
                requester_context(actor_id=DECIDER_ID), approval_id=first.approval_id
            )
        )
        wrong_type = asyncio.run(
            process.request_budget(
                requester_context(actor_type="user"), approval_id=first.approval_id
            )
        )
        other_input = asyncio.run(process.request_budget(approval_id=first.approval_id,
                                                         amount=424242))  # fmt: skip
        assert process.effects == []

        async def continue_concurrently() -> list[Any]:
            return await asyncio.gather(*(process.request_budget(approval_id=first.approval_id)
                                          for _ in range(5)))  # fmt: skip

        continued = asyncio.run(continue_concurrently())
        replay = asyncio.run(process.request_budget(approval_id=first.approval_id))
        effects = process.effects
        with TestClient(core.app()) as client:
            final = approval_view(client, first.approval_id).json()["approval"]
    finally:
        asyncio.run(process.close())

    assert approved.status_code == 200
    decided = approved.json()["approval"]
    assert (decided["status"], decided["decided_by_actor_id"], decided["decision_note"]) == (
        "approved", DECIDER_ID, NOTE)  # fmt: skip
    assert again.status_code == 409  # decisions are final
    # Only the EXACT requester principal, for the EXACT approved input, may continue.
    assert [r.reason for r in (wrong_actor, wrong_type, other_input)] == [R.APPROVAL_MISMATCH] * 3
    # One-time: exactly one continuation executes, concurrent and later ones are refused.
    assert sorted(r.status.value for r in continued) == ["failed"] * 4 + ["verified"]
    assert {r.reason for r in continued if r.status is S.FAILED} == {R.APPROVAL_ALREADY_CONSUMED}
    assert replay.reason is R.APPROVAL_ALREADY_CONSUMED
    assert effects == [(SOUTH, "spring", 150)]
    assert (final["consumed"], final["execution_outcome"]) == (True, "verified")
    (winner,) = [r for r in continued if r.status is S.VERIFIED]
    assert final["consumed_by_action_run_id"] == str(winner.run_id)

    # The authoritative audit lifecycle carries the approval link, the execution and the
    # verification; never the raw parameters or the decision note.
    audit = audit_for(engine, first.approval_id)
    by_run = [a["event_type"] for a in audit if a["run_id"] == winner.run_id]
    assert by_run[-4:] == ["execution_started", "execution_completed", "verification_started",
                           "verified"]  # fmt: skip
    assert [a["event_type"] for a in audit].count("verified") == 1
    assert "awaiting_approval" in {a["event_type"] for a in audit}
    assert {a["action_name"] for a in audit if a["run_id"] == winner.run_id} == {
        "test.budget.update"}  # fmt: skip
    for leaked in ("Spring sale", NOTE, '"amount"', "424242"):
        assert leaked not in repr([a for a in audit if a["action_name"] == "test.budget.update"])
    events = [e["event_type"] for e in rows(
        engine, "SELECT event_type FROM product.approval_events WHERE approval_id = :a "
        "ORDER BY sequence", a=first.approval_id)]  # fmt: skip
    assert events == ["requested", "approved", "execution_claimed", "execution_completed"]
    assert no_outbound_network == []


def test_a_rejected_approval_never_executes(core: CoreInstallation, engine: sa.Engine,
                                           migrated: str) -> None:  # fmt: skip
    process = GovernedTestProcess(migrated, RealClock())
    try:
        first = asyncio.run(process.request_budget(campaign="autumn"))
        with TestClient(core.app()) as client:
            rejected = decide(client, REJECT, first.approval_id, DECIDER, REJECTION)
            approve_after = decide(client, APPROVE, first.approval_id, DECIDER, NOTE)
        refused = asyncio.run(process.request_budget(campaign="autumn",
                                                     approval_id=first.approval_id))  # fmt: skip
        effects = process.effects
    finally:
        asyncio.run(process.close())
    assert rejected.json()["approval"]["status"] == "rejected"
    assert approve_after.status_code == 409
    assert (refused.status, refused.reason) == (S.FAILED, R.APPROVAL_REJECTED)
    assert effects == []
    assert approval_row(engine, first.approval_id)["consumed_by_action_run_id"] is None
    assert "verified" not in {a["event_type"] for a in audit_for(engine, first.approval_id)}


# ----- journey H: a TEST-ONLY Workflow waits for approval, across a restart ------------------


def test_workflow_waits_for_approval_survives_a_restart_and_completes_once(
    core: CoreInstallation, engine: sa.Engine, migrated: str, no_outbound_network
) -> None:
    clock = RealClock()
    process = GovernedTestProcess(migrated, clock)
    try:
        started = asyncio.run(process.start_workflow())
        with pytest.raises(WorkflowNotResumableError):  # never a generic re-open
            asyncio.run(process.workflows.resume(requester_context(), started.run_id))
        first_process = (process.fetch.calls, process.report.calls, process.effects)
        with TestClient(core.app()) as client:
            waiting = run_view(client, started.run_id)
            catalog = client.get("/api/v1/workflows/catalog", headers=OPERATOR).json()
            pending = client.get(APPROVALS, headers=DECIDER,
                                 params={"status": "requested"}).json()["approvals"]  # fmt: skip
    finally:
        asyncio.run(process.close())

    assert (started.status.value, started.failure_code.value) == (
        "awaiting_approval",
        "approval_required",
    )
    assert first_process == (1, 0, [])  # step 1 ran; the write and step 3 did not
    assert (waiting["run"]["status"], waiting["run"]["current_step_id"]) == (
        "awaiting_approval", "budget")  # fmt: skip
    assert [(a["step_id"], a["attempt"], a["status"]) for a in waiting["attempts"]] == [
        ("fetch", 1, "succeeded"), ("budget", 1, "awaiting_approval")]  # fmt: skip
    assert {w["workflow_id"] for w in catalog["workflows"]} == {"operations.daily_report"}
    (approval,) = [a for a in pending
                   if a["source"]["workflow_run_id"] == str(started.run_id)]  # fmt: skip
    approval_id = UUID(approval["approval_id"])
    assert (approval["source"]["kind"], approval["source"]["workflow_step_id"]) == (
        "workflow_step", "budget")  # fmt: skip

    # ---- restart: a NEW installation and a NEW process on the SAME database ---------------
    process = GovernedTestProcess(migrated, clock)
    try:
        with TestClient(core.app()) as client:
            still_waiting = run_view(client, started.run_id)
            approved = decide(client, APPROVE, approval_id, DECIDER, NOTE)
        with pytest.raises(ApprovalAccessDeniedError):  # same id, another actor type
            asyncio.run(process.approvals.resume_workflow(requester_context(actor_type="user"),
                                                          approval_id))  # fmt: skip
        with pytest.raises(ApprovalAccessDeniedError):  # the decider is not the requester
            asyncio.run(process.approvals.resume_workflow(
                requester_context(actor_id=DECIDER_ID), approval_id))  # fmt: skip
        assert process.effects == []

        async def resume_concurrently() -> list[Any]:
            return await asyncio.gather(*(
                process.approvals.resume_workflow(requester_context(), approval_id)
                for _ in range(4)), return_exceptions=True)  # fmt: skip

        outcomes = asyncio.run(resume_concurrently())
        with pytest.raises(ApprovalNotResumableError):
            asyncio.run(process.approvals.resume_workflow(requester_context(), approval_id))
        second_process = (process.fetch.calls, process.report.calls, process.effects)
        with TestClient(core.app()) as client:
            done = run_view(client, started.run_id)
    finally:
        asyncio.run(process.close())

    assert still_waiting["run"]["status"] == "awaiting_approval"
    assert approved.status_code == 200
    succeeded = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(succeeded) == 1 and succeeded[0].status == "succeeded"
    assert all(isinstance(o, ApprovalError) for o in outcomes if o not in succeeded)
    # The completed first step did not re-run; the write ran once; step 3 ran once.
    assert second_process == (0, 1, [(SOUTH, "summer", 175)])
    assert done["run"]["status"] == "succeeded"
    attempts = [(a["step_id"], a["attempt"], a["status"]) for a in done["attempts"]]
    assert attempts == [("fetch", 1, "succeeded"), ("budget", 1, "awaiting_approval"),
                        ("budget", 2, "succeeded"), ("report", 1, "succeeded")]  # fmt: skip
    events = [e["event_type"] for e in done["events"]]
    assert events.count("workflow_approval_resumed") == 1 and "step_retrying" not in events
    assert events[-1] == "workflow_succeeded"
    # The Workflow API shows exactly what the engine persisted; the approval stays bound.
    stored = rows(engine, "SELECT step_id, attempt, status, approval_id FROM "
                  "product.workflow_step_runs WHERE run_id = :r ORDER BY started_at, attempt",
                  r=started.run_id)  # fmt: skip
    assert [(s["step_id"], s["attempt"], s["status"]) for s in stored] == attempts
    assert {s["approval_id"] for s in stored if s["step_id"] == "budget"} == {approval_id}
    (run_row,) = rows(engine, "SELECT status FROM product.workflow_runs WHERE run_id = :r",
                      r=started.run_id)  # fmt: skip
    assert run_row["status"] == done["run"]["status"]
    final = approval_row(engine, approval_id)
    assert (final["status"], final["execution_outcome"]) == ("approved", "verified")
    assert no_outbound_network == []


def test_unknown_approval_and_workflow_ids_fail_closed(core: CoreInstallation) -> None:
    unknown = str(uuid4())
    with TestClient(core.app()) as client:
        approval = client.get(APPROVAL, headers=DECIDER, params={"approval_id": unknown})
        decision = client.post(APPROVE, headers=DECIDER, params={"approval_id": unknown},
                               json={"note": "secret note text"})  # fmt: skip
        run = client.get("/api/v1/workflows/run", headers=OPERATOR, params={"run_id": unknown})
        malformed = client.get(APPROVAL, headers=DECIDER, params={"approval_id": "not-a-uuid"})
    assert approval.status_code == 404 and decision.status_code == 404
    assert run.status_code == 404 and malformed.status_code == 422
    for response in (approval, decision, run, malformed):
        assert set(response.json()) == {"detail"}
        assert "secret note text" not in response.text and "not-a-uuid" not in response.text
        assert "Traceback" not in response.text and "sqlalchemy" not in response.text.lower()
