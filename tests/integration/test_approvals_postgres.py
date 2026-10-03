"""Task 036 on migrated PostgreSQL: the REAL approval repository, broker, governance,
ExecutionCoordinators, ApprovalService, PostgresAuditSink, PostgresWriteCommandStore and
PostgresWorkflowRunRepository, with TEST-ONLY MEDIUM/HIGH actions.

Transitions with their append-only events, one winner for concurrent decisions, exactly
one consumption under concurrency, lazy expiry, the append-only trigger, the two-person
and human-decider CHECK constraints in SQL, company isolation, approval correlation in
the audit trail, WriteCommand and Workflow continuations with one winner, and no raw
parameters in any approval row. No model, no network beyond the local database."""

import asyncio
import socket
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.approval_management.errors import ApprovalConflictError
from app.approval_management.state import ApprovalStatus
from app.commands import CommandStatus, WriteCommandCoordinator
from app.execution import ActionRunReason as R
from app.execution import ActionRunStatus as S
from app.execution import ApprovalClaimStatus
from app.governance import ActionIntent, ActionScope
from app.persistence import (
    PostgresApprovalRepository,
    PostgresAuditSink,
    PostgresWorkflowRunRepository,
    PostgresWriteCommandStore,
    create_product_engine,
)
from app.persistence.database import create_session_factory
from tests.support.approval_fakes import (
    APPROVER,
    BUDGET_UPDATE,
    REQUESTER,
    STORE_A,
    ApprovalWorld,
    actor,
    request,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket.connect

    def local_only(sock: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            raise AssertionError("unexpected outbound connection")
        return real(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


class PG:
    """One real persistence stack (own engine) for one scenario."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_product_engine(database_url)
        sessions = create_session_factory(self.engine)
        self.sessions = sessions
        self.company = str(uuid4())
        self.world = ApprovalWorld(repository=PostgresApprovalRepository(sessions),
                                   audit=PostgresAuditSink(sessions),
                                   decision_audit=PostgresAuditSink(sessions))  # fmt: skip
        self.requester = actor("requester-1", REQUESTER, company=self.company)
        self.approver = actor("approver-1", APPROVER, company=self.company)

    async def pending(self, **kw):
        first = await self.world.run_budget(self.requester, **kw)
        assert first.status is S.AWAITING_APPROVAL and first.approval_id is not None
        return first

    async def close(self) -> None:
        await self.engine.dispose()


def scenario(database_url: str, body):
    async def main():
        pg = PG(database_url)
        try:
            return await body(pg)
        finally:
            await pg.close()

    return asyncio.run(main())


def rows(engine: sa.Engine, sql: str, **params) -> list[dict]:
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(sql), params).mappings()]


def test_full_flow_is_durable_audited_and_consumed_once(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        await pg.world.service.approve(request(pg.approver), first.approval_id, "ok")
        done = await pg.world.run_budget(pg.requester, approval_id=first.approval_id)
        again = await pg.world.run_budget(pg.requester, approval_id=first.approval_id)
        return pg.company, first, done, again

    company, first, done, again = scenario(migrated, body)
    assert (done.status, again.reason) == (S.VERIFIED, R.APPROVAL_ALREADY_CONSUMED)
    (row,) = rows(engine, "SELECT * FROM product.approval_requests WHERE company_id = :c",
                  c=company)  # fmt: skip
    assert row["status"] == "approved" and row["consumed_by_action_run_id"] == done.run_id
    assert row["execution_outcome"] == "verified" and len(row["subject_fingerprint"]) == 64
    assert row["decided_by_actor_id"] == "approver-1" and row["decision_note"] == "ok"
    # No raw parameter mapping: only the fingerprint and the trusted summary (whose
    # description the handler chose to write) are stored.
    assert not {"amount", "campaign", "reason", "parameters", "input"} & set(row)
    assert set(row["summary"]) == {"title", "description", "changes"}
    assert "150" not in row["subject_fingerprint"]
    events = rows(engine, "SELECT event_type, sequence FROM product.approval_events WHERE "
                  "approval_id = :a ORDER BY sequence", a=first.approval_id)  # fmt: skip
    assert [(e["sequence"], e["event_type"]) for e in events] == [
        (1, "requested"),
        (2, "approved"),
        (3, "execution_claimed"),
        (4, "execution_completed"),
    ]
    audit = rows(engine, "SELECT event_type, approval_id, action_name FROM "
                 "product.audit_events WHERE company_id = :c", c=company)  # fmt: skip
    correlated = {a["event_type"] for a in audit if a["approval_id"] == first.approval_id}
    assert {"awaiting_approval", "execution_started", "verified"} <= correlated
    assert "approvals.request.approve" in {a["action_name"] for a in audit}
    # The refused reuse is audited (correlated with the presented approval id).
    refused = [a for a in audit if a["event_type"] == "approval_refused"]
    assert [a["approval_id"] for a in refused] == [first.approval_id]
    assert [a["event_type"] for a in audit if a["approval_id"] == first.approval_id].count(
        "verified"
    ) == 1


def test_concurrent_decisions_have_exactly_one_winner(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        approvers = [actor(f"approver-{i}", APPROVER, company=pg.company) for i in range(8)]
        calls = [pg.world.service.approve(request(a), first.approval_id, None)
                 for a in approvers[:4]]  # fmt: skip
        calls += [pg.world.service.reject(request(a), first.approval_id, "no")
                  for a in approvers[4:]]  # fmt: skip
        return first, await asyncio.gather(*calls, return_exceptions=True)

    first, outcomes = scenario(migrated, body)
    winners = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(winners) == 1
    assert all(isinstance(o, ApprovalConflictError) for o in outcomes if o not in winners)
    decided = rows(engine, "SELECT event_type FROM product.approval_events WHERE "
                   "approval_id = :a AND event_type IN ('approved', 'rejected')",
                   a=first.approval_id)  # fmt: skip
    assert len(decided) == 1


def test_concurrent_consumption_has_exactly_one_winner(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        results = await asyncio.gather(*(
            pg.world.run_budget(pg.requester, approval_id=first.approval_id)
            for _ in range(10)))  # fmt: skip
        return first, results, list(pg.world.budget.effects)

    first, results, effects = scenario(migrated, body)
    assert sorted(r.status.value for r in results) == ["failed"] * 9 + ["verified"]
    assert {r.reason for r in results if r.status is S.FAILED} == {R.APPROVAL_ALREADY_CONSUMED}
    assert len(effects) == 1
    claims = rows(engine, "SELECT count(*) AS n FROM product.approval_events WHERE "
                  "approval_id = :a AND event_type = 'execution_claimed'",
                  a=first.approval_id)  # fmt: skip
    assert claims[0]["n"] == 1


def test_expiry_is_lazy_and_final(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        pg.world.clock.advance(hours=25)
        listed = await pg.world.service.list_requests(request(pg.approver))
        late = await pg.world.run_budget(pg.requester, approval_id=first.approval_id)
        with pytest.raises(ApprovalConflictError):
            await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        return first, listed, late

    first, listed, late = scenario(migrated, body)
    assert [a.status for a in listed] == [ApprovalStatus.EXPIRED]
    assert late.reason is R.APPROVAL_EXPIRED
    (row,) = rows(engine, "SELECT status, decided_by_actor_id FROM product.approval_requests "
                  "WHERE approval_id = :a", a=first.approval_id)  # fmt: skip
    assert row == {"status": "expired", "decided_by_actor_id": None}


def test_events_are_append_only_and_sql_enforces_the_decision_rules(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        return first

    first = scenario(migrated, body)
    for statement in ("UPDATE product.approval_events SET actor_id = 'x' WHERE approval_id = :a",
                      "DELETE FROM product.approval_events WHERE approval_id = :a"):  # fmt: skip
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(sa.text(statement), {"a": first.approval_id})
    for statement in (
        # Two-person rule: the requester can never be the approver.
        "UPDATE product.approval_requests SET decided_by_actor_id = requester_actor_id "
        "WHERE approval_id = :a",
        # Only a human decides.
        "UPDATE product.approval_requests SET decided_by_actor_type = 'system_agent' "
        "WHERE approval_id = :a",
        # Decisions are final states: a consumption needs an approved request.
        "UPDATE product.approval_requests SET status = 'rejected' WHERE approval_id = :a",
        # Only MEDIUM/HIGH risk requests exist.
        "UPDATE product.approval_requests SET risk = 'low_risk_write' WHERE approval_id = :a",
    ):
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            connection.execute(sa.text(statement), {"a": first.approval_id})
    (row,) = rows(engine, "SELECT status, decided_by_actor_id FROM product.approval_requests "
                  "WHERE approval_id = :a", a=first.approval_id)  # fmt: skip
    assert row == {"status": "approved", "decided_by_actor_id": "approver-1"}


def test_another_companys_request_is_invisible(migrated, engine) -> None:
    async def body(pg: PG):
        first = await pg.pending()
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        other = str(uuid4())
        repository = pg.world.repository
        seen = await repository.get(other, first.approval_id)
        events = await repository.events(other, first.approval_id)
        stranger = actor("requester-1", REQUESTER, company=other)
        foreign = await pg.world.run_budget(stranger, approval_id=first.approval_id)
        claim = await repository.claim(
            other, first.approval_id, store_id=STORE_A, action_name=BUDGET_UPDATE.name,
            requester_actor_id="requester-1", requester_actor_type="user",
            subject_fingerprint="0" * 64,
            action_run_id=uuid4(), now=pg.world.clock())  # fmt: skip
        return first, seen, events, foreign, claim

    first, seen, events, foreign, claim = scenario(migrated, body)
    assert (seen, events, claim) == (None, (), ApprovalClaimStatus.NOT_FOUND)
    assert foreign.reason is R.APPROVAL_NOT_FOUND
    (row,) = rows(engine, "SELECT consumed_at FROM product.approval_requests "
                  "WHERE approval_id = :a", a=first.approval_id)  # fmt: skip
    assert row["consumed_at"] is None


def test_write_command_continuation_has_one_winner(migrated, engine) -> None:
    key, params = "budget-change-pg-0001", {"campaign": "spring", "amount": 150,
                                             "reason": "Spring sale"}  # fmt: skip

    async def body(pg: PG):
        commands = WriteCommandCoordinator(PostgresWriteCommandStore(pg.sessions),
                                           pg.world.coordinator, pg.world.catalog,
                                           approvals=pg.world.broker)  # fmt: skip
        scope = ActionScope(company_id=pg.company, store_id=STORE_A)

        def submit(approval_id=None):
            return commands.submit(request(pg.requester), scope,
                                   ActionIntent(name=BUDGET_UPDATE.name), params, key,
                                   approval_id=approval_id)  # fmt: skip

        first = await submit()
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        results = await asyncio.gather(*(submit(first.approval_id) for _ in range(10)))
        return pg.company, first, results, list(pg.world.budget.effects)

    company, first, results, effects = scenario(migrated, body)
    assert first.status is CommandStatus.AWAITING_APPROVAL
    assert [r.replayed for r in results].count(False) == 1 and len(effects) == 1
    winner = [r for r in results if not r.replayed]
    assert winner[0].status is CommandStatus.VERIFIED
    # Losers replay the command as it was when they looked: in flight, or already done.
    assert {r.status for r in results} <= {CommandStatus.VERIFIED, CommandStatus.IN_PROGRESS}
    (row,) = rows(engine, "SELECT * FROM product.write_commands WHERE company_id = :c",
                  c=company)  # fmt: skip
    assert row["status"] == "verified" and row["approval_id"] == first.approval_id
    assert "Spring sale" not in repr(row) and "spring" not in repr(row)


def test_workflow_continuation_has_one_winner_and_links_the_attempt(migrated, engine) -> None:
    from app.approval_management.service import ApprovalService
    from app.workflow_management.engine import WorkflowEngine
    from app.workflow_management.handlers import (
        WorkflowRuntimeRegistration,
        WorkflowRuntimeRegistry,
    )
    from tests.approval_management.test_workflow_approval import (
        APPROVAL_WORKFLOW,
        CATALOG,
        BudgetRunInput,
        BudgetWrite,
        FetchCampaign,
        Report,
    )
    from tests.support.workflow_fakes import StepClock

    async def body(pg: PG):
        report = Report()
        bindings = WorkflowRuntimeRegistry(CATALOG, [WorkflowRuntimeRegistration(
            APPROVAL_WORKFLOW.workflow_id, BudgetRunInput,
            (FetchCampaign(), BudgetWrite(pg.world.coordinator), report))])  # fmt: skip
        engine_ = WorkflowEngine(CATALOG, bindings, PostgresWorkflowRunRepository(pg.sessions),
                                 clock=StepClock())  # fmt: skip
        w = pg.world
        service = ApprovalService(w.repository, w.service._gate, w.service._coordinator,
                                  clock=w.clock, workflows=engine_)  # fmt: skip
        started = await engine_.execute(
            APPROVAL_WORKFLOW.workflow_id, request(pg.requester),
            ActionScope(company_id=pg.company, store_id=STORE_A),
            {"campaign": "spring", "amount": 150})  # fmt: skip
        (approval,) = await w.repository.list(pg.company, status=None, action_name=None,
                                              limit=5)  # fmt: skip
        await w.service.approve(request(pg.approver), approval.approval_id, None)
        outcomes = await asyncio.gather(*(
            service.resume_workflow(request(pg.requester), approval.approval_id)
            for _ in range(5)), return_exceptions=True)  # fmt: skip
        return started, approval, outcomes, list(w.budget.effects), report.calls

    started, approval, outcomes, effects, reports = scenario(migrated, body)
    assert started.status.value == "awaiting_approval"
    succeeded = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(succeeded) == 1 and succeeded[0].status == "succeeded"
    assert len(effects) == 1 and reports == 1
    attempts = rows(engine, "SELECT attempt, status, approval_id FROM "
                    "product.workflow_step_runs WHERE run_id = :r AND step_id = 'budget' "
                    "ORDER BY attempt", r=started.run_id)  # fmt: skip
    assert [(a["attempt"], a["status"]) for a in attempts] == [(1, "awaiting_approval"),
                                                               (2, "succeeded")]  # fmt: skip
    assert {a["approval_id"] for a in attempts} == {approval.approval_id}
    events = [e["event_type"] for e in rows(
        engine, "SELECT event_type FROM product.workflow_events WHERE run_id = :r "
        "ORDER BY sequence", r=started.run_id)]  # fmt: skip
    assert events.count("workflow_approval_resumed") == 1 and events[-1] == "workflow_succeeded"


def test_deployment_app_serves_an_empty_approval_inbox(settings, runtime_settings,
                                                       migrated) -> None:  # fmt: skip
    from fastapi.testclient import TestClient

    from app.bootstrap import create_deployment_app
    from tests.support.product_auth import TEST_PRODUCT_KEY, deployment_settings, principal

    company = str(uuid4())
    keys = (principal(TEST_PRODUCT_KEY, permissions=APPROVER),)
    configured = deployment_settings(settings, "test", business_backend="disabled",
                                     product_api_keys=keys, company_id=company)  # fmt: skip
    app = create_deployment_app(configured, runtime_settings)
    headers = {"Authorization": f"Bearer {TEST_PRODUCT_KEY}"}
    with TestClient(app) as client:
        listed = client.get("/api/v1/approvals", headers=headers)
        assert listed.status_code == 200 and listed.json()["approvals"] == []  # no fake data
        missing = client.get("/api/v1/approvals/approval", headers=headers,
                             params={"approval_id": str(uuid4())})  # fmt: skip
        assert missing.status_code == 404
        assert client.get("/api/v1/approvals").status_code == 401


def test_same_actor_id_with_another_actor_type_never_claims(migrated, engine) -> None:
    """The requester principal is (actor id, actor type): the same id as an api_client
    or system_agent is a different principal. Proven on the real CAS: even a claim that
    presents the exact subject fingerprint of the user principal but another actor type
    matches nothing and changes nothing."""
    from app.approval_management.fingerprint import subject_fingerprint
    from tests.support.approval_fakes import BudgetInput

    async def body(pg: PG):
        first = await pg.pending()  # requested by ("requester-1", "user")
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        twins = [actor("requester-1", REQUESTER, company=pg.company, actor_type=kind)
                 for kind in ("api_client", "system_agent")]  # fmt: skip
        via_coordinator = [await pg.world.run_budget(t, approval_id=first.approval_id)
                           for t in twins]  # fmt: skip
        validated = BudgetInput(campaign="spring", amount=150, reason="Spring sale")
        user_print = subject_fingerprint(
            action_name=BUDGET_UPDATE.name, requester_actor_id="requester-1",
            requester_actor_type="user", company_id=pg.company, store_id=STORE_A,
            validated_input=validated)  # fmt: skip
        direct = await pg.world.repository.claim(
            pg.company, first.approval_id, store_id=STORE_A, action_name=BUDGET_UPDATE.name,
            requester_actor_id="requester-1", requester_actor_type="api_client",
            subject_fingerprint=user_print, action_run_id=uuid4(),
            now=pg.world.clock())  # fmt: skip
        unconsumed = await pg.world.repository.get(pg.company, first.approval_id)
        # Concurrent: 5 twins and 5 real requesters; only the real principal can win.
        race = await asyncio.gather(
            *(pg.world.run_budget(twins[0], approval_id=first.approval_id) for _ in range(5)),
            *(pg.world.run_budget(pg.requester, approval_id=first.approval_id)
              for _ in range(5)))  # fmt: skip
        return first, via_coordinator, direct, unconsumed, race, list(pg.world.budget.effects)

    first, via_coordinator, direct, unconsumed, race, effects = scenario(migrated, body)
    assert [r.reason for r in via_coordinator] == [R.APPROVAL_MISMATCH, R.APPROVAL_MISMATCH]
    assert direct is ApprovalClaimStatus.MISMATCH and unconsumed.consumed_at is None
    assert [r.reason for r in race[:5]] == [R.APPROVAL_MISMATCH] * 5
    assert sorted(r.status.value for r in race[5:]) == ["failed"] * 4 + ["verified"]
    assert len(effects) == 1
    (row,) = rows(engine, "SELECT consumed_by_action_run_id FROM product.approval_requests "
                  "WHERE approval_id = :a", a=first.approval_id)  # fmt: skip
    assert row["consumed_by_action_run_id"] in {r.run_id for r in race[5:]}
    claimed = rows(engine, "SELECT actor_id, actor_type FROM product.approval_events WHERE "
                   "approval_id = :a AND event_type = 'execution_claimed'",
                   a=first.approval_id)  # fmt: skip
    assert claimed == [{"actor_id": "requester-1", "actor_type": "user"}]


def test_wrong_actor_type_never_touches_an_approval_linked_command(migrated, engine) -> None:
    """PostgresWriteCommandStore + PostgresApprovalRepository: a same-id principal of
    another actor type finds the requester's command (namespace (company, actor id, key))
    but changes nothing; the exact requester then continues it exactly once, also when
    both race."""
    from app.commands.errors import ApprovalContinuationRefusedError

    key = "budget-change-pg-twin-0001"
    params = {"campaign": "spring", "amount": 150, "reason": "Spring sale"}

    async def body(pg: PG):
        commands = WriteCommandCoordinator(PostgresWriteCommandStore(pg.sessions),
                                           pg.world.coordinator, pg.world.catalog,
                                           approvals=pg.world.broker)  # fmt: skip
        scope = ActionScope(company_id=pg.company, store_id=STORE_A)
        twin = actor("requester-1", REQUESTER, company=pg.company, actor_type="api_client")

        def submit(who, approval_id=None):
            return commands.submit(request(who), scope, ActionIntent(name=BUDGET_UPDATE.name),
                                   params, key, approval_id=approval_id)  # fmt: skip

        first = await submit(pg.requester)
        await pg.world.service.approve(request(pg.approver), first.approval_id, None)
        refused = []
        for approval_id in (first.approval_id, None):
            try:
                await submit(twin, approval_id)
            except ApprovalContinuationRefusedError as error:
                refused.append(error)
        after_twin = rows(engine, "SELECT status, approval_id FROM product.write_commands "
                          "WHERE command_id = :c", c=first.command_id)  # fmt: skip
        unconsumed = await pg.world.repository.get(pg.company, first.approval_id)
        outcomes = await asyncio.gather(
            *(submit(twin, first.approval_id) for _ in range(5)),
            *(submit(pg.requester, first.approval_id) for _ in range(5)),
            return_exceptions=True)  # fmt: skip
        return first, refused, after_twin, unconsumed, outcomes, list(pg.world.budget.effects)

    first, refused, after_twin, unconsumed, outcomes, effects = scenario(migrated, body)
    assert len(refused) == 2
    assert after_twin == [{"status": "awaiting_approval", "approval_id": first.approval_id}]
    assert unconsumed.consumed_at is None
    assert all(isinstance(o, ApprovalContinuationRefusedError) for o in outcomes[:5])
    right = outcomes[5:]
    assert not any(isinstance(o, Exception) for o in right)
    assert [o.replayed for o in right].count(False) == 1 and len(effects) == 1
    (row,) = rows(engine, "SELECT status FROM product.write_commands WHERE command_id = :c",
                  c=first.command_id)  # fmt: skip
    assert row["status"] == "verified"
    claims = rows(engine, "SELECT actor_type FROM product.approval_events WHERE "
                  "approval_id = :a AND event_type = 'execution_claimed'",
                  a=first.approval_id)  # fmt: skip
    assert claims == [{"actor_type": "user"}]
