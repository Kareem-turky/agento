"""The Workflow Platform on REAL PostgreSQL (migration 0005) — Task 034.

Every "process" below has its OWN SQLAlchemy engine and connection pool. Proves the
PostgreSQL repository's durable lifecycle, append-only events, compare-and-set execution
claims (concurrent executors, stale executors, expired claims), crash/restart recovery
without re-running completed Steps, company-scoped reads, fail-closed storage, and the
daily operations report running as ``operations.daily_report`` through the deployment
without persisting the report.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.bootstrap import create_deployment_app
from app.integrations.commerce.mock import EntityType, canonical_id
from app.persistence import (
    PostgresWorkflowRunRepository,
    create_product_engine,
    create_session_factory,
)
from app.workflow_management.contracts import (
    Claim,
    RunChange,
    WorkflowClaimConflictError,
    WorkflowRepositoryError,
)
from app.workflow_management.errors import WorkflowLeaseConflictError, WorkflowUnavailableError
from app.workflow_management.state import (
    StepAttemptStatus,
    VerificationCode,
    WorkflowEventType,
    WorkflowFailureCode,
    WorkflowRunStatus,
)
from tests.conftest import UNREACHABLE_DATABASE_URL
from tests.support.product_auth import deployment_settings, principal
from tests.support.scripted_tool_model import ScriptedToolModel
from tests.support.workflow_fakes import (
    StepClock,
    TestInput,
    engine,
    registry,
    request,
    scope,
    three_step_handlers,
)

pytestmark = pytest.mark.integration
R = WorkflowRunStatus


def run[T](coro) -> T:
    return asyncio.run(coro)


@asynccontextmanager
async def process(database_url: str):
    """One 'process': its own engine/pool and repository."""
    db = create_product_engine(database_url)
    try:
        yield PostgresWorkflowRunRepository(create_session_factory(db))
    finally:
        await db.dispose()


def rows(engine_: sa.Engine, table: str, run_id) -> list[dict[str, Any]]:
    order = {"workflow_runs": "run_id", "workflow_step_runs": "started_at, attempt",
             "workflow_events": "sequence"}[table]  # fmt: skip
    with engine_.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(
            f"SELECT * FROM product.{table} WHERE run_id = :r ORDER BY {order}"  # noqa: S608
        ), {"r": run_id}).mappings()]  # fmt: skip


class Crash(BaseException):
    pass


def company() -> str:
    return f"wf-company-{uuid4()}"


# ----- lifecycle --------------------------------------------------------------------------------


def test_a_run_is_durable_control_state_only(migrated: str, engine: sa.Engine) -> None:
    tenant = company()

    async def scenario():
        async with process(migrated) as repository:
            platform = engine_for(repository, three_step_handlers(fetch=["error", "ok"]))
            return await platform.execute("testing.three_steps", request(tenant), scope(tenant),
                                          TestInput(value=31337))  # fmt: skip

    result = run(scenario())
    assert result.status is R.SUCCEEDED and result.output("finish") == {"result": 62674}
    (row,) = rows(engine, "workflow_runs", result.run_id)
    assert (row["status"], row["company_id"], row["lease_owner"], row["failure_code"]) == (
        "succeeded", tenant, None, None)  # fmt: skip
    assert row["input_state"] == {"value": 31337} and len(row["input_fingerprint"]) == 64
    attempts = rows(engine, "workflow_step_runs", result.run_id)
    assert [(a["step_id"], a["attempt"], a["status"], a["checkpoint"]) for a in attempts] == [
        ("fetch", 1, "failed", None), ("fetch", 2, "succeeded", {"value": 31337}),
        ("transform", 1, "succeeded", {"doubled": 62674}), ("finish", 1, "succeeded", None),
    ]  # fmt: skip
    events = rows(engine, "workflow_events", result.run_id)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert [e["event_type"] for e in events][:5] == [
        "workflow_requested",
        "workflow_started",
        "step_started",
        "step_failed",
        "step_retrying",
    ]
    # Never a Step output, raw error text or the step result in durable state.
    text = json.dumps([row, attempts, events], default=str)
    assert "result" not in text.replace("_result", "") and "SENSITIVE" not in text


def engine_for(repository, handlers, clock=None):
    return engine(repository, registry(three=handlers), clock)


def test_workflow_events_are_append_only(migrated: str, engine: sa.Engine) -> None:
    tenant = company()

    async def scenario():
        async with process(migrated) as repository:
            platform = engine_for(repository, three_step_handlers())
            return await platform.execute("testing.three_steps", request(tenant), scope(tenant),
                                          TestInput(value=1))  # fmt: skip

    result = run(scenario())
    update = "UPDATE product.workflow_events SET event_type = 'step_failed' WHERE run_id = :r"
    delete = "DELETE FROM product.workflow_events WHERE run_id = :r"
    for statement in (update, delete):
        with pytest.raises(sa.exc.DBAPIError) as info:
            with engine.begin() as connection:
                connection.execute(sa.text(statement), {"r": result.run_id})
        assert "append-only" in str(info.value)
    assert len(rows(engine, "workflow_events", result.run_id)) == 9


def test_the_schema_refuses_malformed_state(migrated: str, engine: sa.Engine) -> None:
    tenant = company()

    async def scenario():
        async with process(migrated) as repository:
            platform = engine_for(repository, three_step_handlers())
            return await platform.execute("testing.three_steps", request(tenant), scope(tenant),
                                          TestInput(value=1))  # fmt: skip

    result = run(scenario())
    for statement in (
        "UPDATE product.workflow_runs SET status = 'exploded' WHERE run_id = :r",
        "UPDATE product.workflow_runs SET failure_code = 'Traceback' WHERE run_id = :r",
        "UPDATE product.workflow_runs SET completed_at = NULL WHERE run_id = :r",
        "UPDATE product.workflow_step_runs SET checkpoint = '[1]'::jsonb WHERE run_id = :r",
        "UPDATE product.workflow_step_runs SET status = 'failed', failure_code = "
        "'step_timeout' WHERE run_id = :r AND checkpoint IS NOT NULL",
    ):
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(sa.text(statement), {"r": result.run_id})


# ----- recovery ---------------------------------------------------------------------------------


def test_crash_and_resume_in_a_new_process_never_reruns_a_completed_step(
    migrated: str, engine: sa.Engine
) -> None:
    tenant = company()
    clock = StepClock()
    first = three_step_handlers()
    first["transform"].produce = lambda *a: (_ for _ in ()).throw(Crash())

    async def crashing():
        async with process(migrated) as repository:
            platform = engine_for(repository, first, clock)
            await platform.execute("testing.three_steps", request(tenant), scope(tenant),
                                   TestInput(value=5))  # fmt: skip

    with pytest.raises(Crash):
        run(crashing())
    with engine.connect() as connection:
        (run_id,) = connection.execute(sa.text(
            "SELECT run_id FROM product.workflow_runs WHERE company_id = :c"), {"c": tenant},
        ).scalars().all()  # fmt: skip
    assert [(a["step_id"], a["status"]) for a in rows(engine, "workflow_step_runs", run_id)] == [
        ("fetch", "succeeded"),
        ("transform", "running"),
    ]
    second = three_step_handlers()

    async def resume():
        async with process(migrated) as repository:  # a NEW process (engine, pool, executor)
            platform = engine_for(repository, second, clock)
            with pytest.raises(WorkflowLeaseConflictError):  # the old claim is still live
                await platform.resume(request(tenant), run_id)
            clock.advance(3600)  # the lost executor's claim expires
            return await platform.resume(request(tenant), run_id)

    result = run(resume())
    assert (result.run_id, result.status) == (run_id, R.SUCCEEDED)
    assert second["fetch"].calls == []  # NOT executed again
    assert second["transform"].calls[0][1]["fetch"].value == 5  # reloaded checkpoint
    assert [(a["step_id"], a["attempt"], a["status"], a["failure_code"])
            for a in rows(engine, "workflow_step_runs", run_id)] == [
        ("fetch", 1, "succeeded", None), ("transform", 1, "failed", "executor_lost"),
        ("transform", 2, "succeeded", None), ("finish", 1, "succeeded", None),
    ]  # fmt: skip
    assert "workflow_resumed" in [e["event_type"] for e in rows(engine, "workflow_events", run_id)]


# ----- execution claims -------------------------------------------------------------------------


def test_concurrent_executors_claim_a_run_exactly_once(migrated: str, engine: sa.Engine) -> None:
    tenant = company()
    clock = StepClock()
    handlers = three_step_handlers(transform=["approval"])

    async def scenario():
        async with process(migrated) as setup:
            platform = engine_for(setup, handlers, clock)
            # Create a run left pending (as if its creator died right after creating it).
            platform_run = await platform.execute("testing.three_steps", request(tenant),
                                                  scope(tenant), TestInput(value=1))  # fmt: skip
        return platform_run

    finished = run(scenario())
    assert finished.status is R.AWAITING_APPROVAL  # terminal: nobody can claim it any more

    async def race():
        pending = await create_pending(migrated, tenant, clock)
        async with process(migrated) as a, process(migrated) as b:
            now = clock()
            outcomes = await asyncio.gather(
                *(repo.claim(tenant, pending, uuid4(), now, now + timedelta(seconds=60))
                  for repo in (a, b, a, b)), return_exceptions=True)  # fmt: skip
            terminal = await asyncio.gather(
                a.claim(tenant, finished.run_id, uuid4(), now, now), return_exceptions=True
            )
        return outcomes, terminal

    outcomes, terminal = run(race())
    winners = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(winners) == 1  # exactly one executor holds the run
    assert all(isinstance(o, WorkflowClaimConflictError) for o in outcomes if o not in winners)
    assert isinstance(terminal[0], WorkflowClaimConflictError)


async def create_pending(database_url: str, tenant: str, clock) -> Any:
    from app.workflow_management.records import NewWorkflowEvent, WorkflowRunRecord

    now = clock()
    record = WorkflowRunRecord(
        run_id=uuid4(), workflow_id="testing.three_steps", workflow_version=1,
        request_id=uuid4(), company_id=tenant, actor_id="user-1", actor_type="user",
        channel="api", store_id="store-a", status=R.PENDING, current_step_id=None,
        failure_code=None, input_state={"value": 1}, input_fingerprint="0" * 64,
        lease_owner=None, lease_expires_at=None, created_at=now, updated_at=now,
        completed_at=None,
    )  # fmt: skip
    async with process(database_url) as repository:
        await repository.create_run(
            record,
            NewWorkflowEvent(
                event_type=WorkflowEventType.WORKFLOW_REQUESTED, status="pending", occurred_at=now
            ),
        )
    return record.run_id


def test_a_stale_executor_cannot_write_after_a_takeover(migrated: str, engine: sa.Engine) -> None:
    tenant = company()
    clock = StepClock()

    async def scenario():
        run_id = await create_pending(migrated, tenant, clock)
        async with process(migrated) as stale, process(migrated) as fresh:
            now = clock()
            old = uuid4()
            await stale.claim(tenant, run_id, old, now, now + timedelta(seconds=30))
            # The claim is live: a second executor is refused.
            with pytest.raises(WorkflowClaimConflictError):
                await fresh.claim(tenant, run_id, uuid4(), clock(), clock.now)
            clock.advance(60)  # expired: recoverable by another executor
            new = uuid4()
            await fresh.claim(tenant, run_id, new, clock(), clock.now + timedelta(seconds=30))
            change = RunChange(expected_status=R.PENDING, status=R.RUNNING, current_step_id=None,
                               lease_expires_at=clock.now + timedelta(seconds=30),
                               updated_at=clock())  # fmt: skip
            with pytest.raises(WorkflowClaimConflictError):  # the stale token writes nothing
                await stale.advance(Claim(run_id=run_id, company_id=tenant, token=old), change)
            with pytest.raises(LookupError):  # another company sees no such run
                await fresh.claim("someone-else", run_id, uuid4(), clock(), clock.now)
            return await fresh.advance(Claim(run_id=run_id, company_id=tenant, token=new),
                                       change)  # fmt: skip

    record = run(scenario())
    assert record.status is R.RUNNING
    (row,) = rows(engine, "workflow_runs", record.run_id)
    assert row["status"] == "running"
    assert [e["event_type"] for e in rows(engine, "workflow_events", record.run_id)] == [
        "workflow_requested"
    ]


def test_a_finished_attempt_is_never_finished_again(migrated: str, engine: sa.Engine) -> None:
    tenant = company()
    clock = StepClock()

    async def scenario():
        from app.workflow_management.contracts import AttemptFinish
        from app.workflow_management.records import StepAttemptRecord

        run_id = await create_pending(migrated, tenant, clock)
        async with process(migrated) as repository:
            token = uuid4()
            claim = Claim(run_id=run_id, company_id=tenant, token=token)
            await repository.claim(tenant, run_id, token, clock(), clock.now + timedelta(hours=1))
            start = StepAttemptRecord(
                run_id=run_id, company_id=tenant, step_id="fetch", attempt=1,
                handler_id="testing.fetch", status=StepAttemptStatus.RUNNING, failure_code=None,
                verification_code=None, checkpoint=None, started_at=clock(),
                completed_at=None)  # fmt: skip
            lease = clock.now + timedelta(hours=1)
            await repository.advance(claim, RunChange(
                expected_status=R.PENDING, status=R.RUNNING, current_step_id="fetch",
                lease_expires_at=lease, updated_at=clock(), start_attempt=start))  # fmt: skip
            finish = AttemptFinish(step_id="fetch", attempt=1, status=StepAttemptStatus.FAILED,
                                   failure_code=WorkflowFailureCode.STEP_EXECUTION_FAILED,
                                   completed_at=clock())  # fmt: skip
            change = RunChange(expected_status=R.RUNNING, status=R.RUNNING,
                               current_step_id="fetch", lease_expires_at=lease,
                               updated_at=clock(), finish_attempt=finish)  # fmt: skip
            await repository.advance(claim, change)
            succeeded = AttemptFinish(step_id="fetch", attempt=1,
                                      status=StepAttemptStatus.SUCCEEDED,
                                      verification_code=VerificationCode.VERIFIED,
                                      completed_at=clock())  # fmt: skip
            with pytest.raises(WorkflowRepositoryError):  # history is never overwritten
                await repository.advance(claim, change.model_copy(
                    update={"finish_attempt": succeeded}))  # fmt: skip
            return run_id

    run_id = run(scenario())
    ((attempt,),) = [rows(engine, "workflow_step_runs", run_id)]
    assert (attempt["status"], attempt["failure_code"]) == ("failed", "step_execution_failed")


# ----- storage failures and company scope -----------------------------------------------------


def test_unreachable_storage_fails_closed_before_any_step() -> None:
    handlers = three_step_handlers()

    async def scenario():
        async with process(UNREACHABLE_DATABASE_URL) as repository:
            platform = engine_for(repository, handlers)
            await platform.execute("testing.three_steps", request(), scope(), TestInput(value=1))

    with pytest.raises(WorkflowUnavailableError):
        run(scenario())
    assert handlers["fetch"].calls == []


def test_reads_are_company_scoped_in_the_query(migrated: str) -> None:
    tenant, other = company(), company()

    async def scenario():
        async with process(migrated) as repository:
            platform = engine_for(repository, three_step_handlers())
            mine = await platform.execute("testing.three_steps", request(tenant), scope(tenant),
                                          TestInput(value=1))  # fmt: skip
            theirs = await platform.execute("testing.three_steps", request(other), scope(other),
                                            TestInput(value=2))  # fmt: skip
            listed = await repository.list_runs(tenant, 10)
            return (
                mine,
                theirs,
                listed,
                await repository.get_run(tenant, theirs.run_id),
                (
                    await repository.list_attempts(tenant, theirs.run_id),
                    await repository.list_events(tenant, theirs.run_id),
                ),
            )

    mine, theirs, listed, foreign, (attempts, events) = run(scenario())
    assert [(r.run_id, n) for r, n in listed] == [(mine.run_id, 3)]
    assert foreign is None and attempts == () and events == ()


# ----- the daily report through the deployment ------------------------------------------------

DEMO_COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))
REPORT_KEY = "test-workflow-report-key-" + "k" * 24
READER_KEY = "test-workflow-reader-key-" + "w" * 24


def daily_runs(engine_: sa.Engine) -> list[dict[str, Any]]:
    with engine_.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(
            "SELECT * FROM product.workflow_runs WHERE company_id = :c "
            "AND workflow_id = 'operations.daily_report' ORDER BY created_at"),
            {"c": DEMO_COMPANY}).mappings()]  # fmt: skip


def test_the_daily_report_runs_as_a_durable_product_workflow(
    settings, runtime_settings, migrated: str, engine: sa.Engine
) -> None:
    keys = (principal(REPORT_KEY, key_id="report", actor_id="workflow-report-actor",
                      permissions=frozenset({"stores.read", "orders.read", "shipments.read"}),
                      store_ids=frozenset({SOUTH})),
            principal(READER_KEY, key_id="wf-reader", actor_id="workflow-reader",
                      permissions=frozenset({"workflows.read"})))  # fmt: skip
    configured = deployment_settings(settings, "test", company_id=DEMO_COMPANY,
                                     business_backend="mock", product_api_keys=keys)  # fmt: skip
    model = ScriptedToolModel()
    before = {r["run_id"] for r in daily_runs(engine)}
    app = create_deployment_app(configured, runtime_settings, model=model)
    with TestClient(app) as client:
        answer = client.get("/api/v1/operations/reports/daily",
                            params={"store_id": SOUTH, "business_date": "2026-03-03"},
                            headers={"Authorization": f"Bearer {REPORT_KEY}"})  # fmt: skip
        assert answer.status_code == 200
        report = answer.json()["report"]
        new = [r for r in daily_runs(engine) if r["run_id"] not in before]
        (row,) = new
        assert (row["status"], row["store_id"], row["actor_id"]) == (
            "succeeded", SOUTH, "workflow-report-actor")  # fmt: skip
        assert row["input_state"] == {"business_date": "2026-03-03"}
        assert row["request_id"] == UUID(answer.json()["request_id"])
        (attempt,) = rows(engine, "workflow_step_runs", row["run_id"])
        assert (attempt["step_id"], attempt["status"], attempt["checkpoint"]) == (
            "compute_daily_report", "succeeded", None)  # fmt: skip
        # The report is NEVER persisted by the platform.
        stored = json.dumps([row, attempt, rows(engine, "workflow_events", row["run_id"])],
                            default=str)  # fmt: skip
        for value in (report["timezone"], "orders_created", "findings", "metrics",
                      *(f["entity_id"] for f in report["findings"])):  # fmt: skip
            assert value not in stored, value
        # ... and the run is visible (read-only) to a workflows.read key of the company.
        reader = {"Authorization": f"Bearer {READER_KEY}"}
        listed = client.get("/api/v1/workflows/runs", headers=reader).json()["runs"]
        assert str(row["run_id"]) in {r["run_id"] for r in listed}
        detail = client.get("/api/v1/workflows/run", params={"run_id": str(row["run_id"])},
                            headers=reader).json()  # fmt: skip
        assert [e["event_type"] for e in detail["events"]] == [
            "workflow_requested", "workflow_started", "step_started", "step_succeeded",
            "workflow_succeeded"]  # fmt: skip
        # The report key has no workflows.read; the reader cannot run a report.
        denied = client.get("/api/v1/workflows/runs",
                            headers={"Authorization": f"Bearer {REPORT_KEY}"})  # fmt: skip
        assert denied.status_code == 403
        catalog = client.get("/api/v1/workflows/catalog", headers=reader).json()
        assert [w["workflow_id"] for w in catalog["workflows"]] == ["operations.daily_report"]
    assert model.requests == []  # the platform made ZERO model calls
