"""The daily operations report on the Workflow Platform (Task 034).

The workflow-backed service keeps the ``DailyOperationsReportService`` contract exactly:
the SAME report the existing DailyOperationsWorkflow computes, the same denial and the
same "unavailable". Each report is one durable ``operations.daily_report`` run whose state
holds execution control only: never the report, metrics, findings or store data.
"""

import json
from datetime import date, datetime
from uuid import UUID

import pytest

from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsReport,
    DailyOperationsUnavailableError,
)
from app.workflow_management import build_default_workflow_catalog
from app.workflow_management.engine import WorkflowEngine
from app.workflow_management.handlers import StepOutcome, StepResult, WorkflowRuntimeRegistry
from app.workflows.operations_daily_platform import (
    DailyReportInput,
    DailyReportStepHandler,
    WorkflowBackedDailyOperationsReportService,
    daily_report_registration,
)
from tests.support.workflow_fakes import InMemoryWorkflowRunRepository, StepClock
from tests.workflows.helpers import (
    NORTH,
    SOUTH,
    SpyCommerce,
    request,
    run,
    scope,
    workflow,
)


def platform(report=None, repository=None):
    report = report if report is not None else workflow()
    repository = repository if repository is not None else InMemoryWorkflowRunRepository()
    catalog = build_default_workflow_catalog()
    bindings = WorkflowRuntimeRegistry(catalog, [daily_report_registration(report)])
    engine = WorkflowEngine(catalog, bindings, repository, clock=StepClock())
    return WorkflowBackedDailyOperationsReportService(engine), repository, report


def stored(repository) -> str:
    return json.dumps([list(repository.runs.values()), list(repository.attempts.values()),
                       repository.events], default=str)  # fmt: skip


def test_the_report_is_exactly_the_existing_workflow_report() -> None:
    service, repository, report_workflow = platform()
    req = request()
    via_platform = run(service.get_daily_report(req, scope(), date(2026, 3, 3)))
    direct = run(workflow().get_daily_report(req, scope(), date(2026, 3, 3)))
    assert isinstance(via_platform, DailyOperationsReport)
    assert via_platform == direct  # the platform never recomputes or alters it
    # One durable run of operations.daily_report with one verified attempt.
    ((run_id, row),) = repository.runs.items()
    assert (row["workflow_id"], row["status"], row["store_id"]) == (
        "operations.daily_report", "succeeded", SOUTH)  # fmt: skip
    assert row["input_state"] == {"business_date": "2026-03-03"}
    (attempt,) = repository.attempt_rows(run_id)
    assert (attempt["step_id"], attempt["status"], attempt["checkpoint"]) == (
        "compute_daily_report", "succeeded", None)  # fmt: skip


def test_the_report_is_never_persisted() -> None:
    service, repository, _ = platform()
    report = run(service.get_daily_report(request(), scope(), date(2026, 3, 3)))
    text = stored(repository)
    for value in ("orders_created", "findings", "metrics", "shipment", "Europe/Berlin",
                  report.timezone, *(str(f.entity_id) for f in report.findings)):  # fmt: skip
        assert value not in text, value


def test_omitted_date_is_still_decided_by_the_store_timezone() -> None:
    service, repository, _ = platform()
    report = run(service.get_daily_report(request(), scope(), None))
    assert report.business_date == date(2026, 3, 6)  # FIXED_NOW in Europe/Berlin
    ((_, row),) = repository.runs.items()
    assert row["input_state"] == {"business_date": None}


def test_a_denial_stays_a_denial_and_reads_nothing() -> None:
    commerce = SpyCommerce()
    service, repository, _ = platform(workflow(commerce))
    limited = request(permissions=frozenset({"orders.read"}))
    with pytest.raises(DailyOperationsForbiddenError):
        run(service.get_daily_report(limited, scope(), None))
    assert commerce.calls == []
    ((_, row),) = repository.runs.items()
    assert (row["status"], row["failure_code"]) == ("failed", "access_denied")
    # No trusted actor, or a scope outside the actor's company: denied, no run recorded.
    from app.context.models import RequestContext

    before = len(repository.runs)
    with pytest.raises(DailyOperationsForbiddenError):
        run(service.get_daily_report(RequestContext(), scope(), None))
    with pytest.raises(DailyOperationsForbiddenError):
        run(service.get_daily_report(request(), scope(company=str(UUID(int=7))), None))
    assert len(repository.runs) == before


def test_unavailable_stays_unavailable() -> None:
    class Broken(SpyCommerce):
        async def get_store(self, store_id):
            raise RuntimeError("SENSITIVE-provider-detail")

    service, repository, _ = platform(workflow(Broken()))
    with pytest.raises(DailyOperationsUnavailableError):
        run(service.get_daily_report(request(), scope(), None))
    ((_, row),) = repository.runs.items()
    assert (row["status"], row["failure_code"]) == ("failed", "step_execution_failed")
    assert "SENSITIVE" not in stored(repository)
    for bad in ("2026-03-03", datetime(2026, 3, 3)):
        with pytest.raises(DailyOperationsUnavailableError):
            run(service.get_daily_report(request(), scope(), bad))  # type: ignore[arg-type]
    with pytest.raises(DailyOperationsUnavailableError):
        run(service.get_daily_report(object(), scope(), None))  # type: ignore[arg-type]


def test_lost_durable_state_answers_unavailable_never_a_report() -> None:
    repository = InMemoryWorkflowRunRepository()
    repository.fail_on = {"advance"}
    commerce = SpyCommerce()
    service, _, _ = platform(workflow(commerce), repository)
    with pytest.raises(DailyOperationsUnavailableError):
        run(service.get_daily_report(request(), scope(), None))
    assert commerce.calls == []


def test_verification_rejects_a_report_for_another_store_or_date() -> None:
    report = run(workflow().get_daily_report(request(), scope(), date(2026, 3, 3)))
    handler = DailyReportStepHandler(workflow())
    service, repository, _ = platform()
    from app.workflow_management.handlers import WorkflowStepContext

    def context(store: str) -> WorkflowStepContext:
        req = request()
        return WorkflowStepContext(
            workflow_run_id=UUID(int=1), workflow_id="operations.daily_report",
            workflow_version=1, step_id="compute_daily_report", attempt=1,
            request_id=req.request_id, actor=req.actor, company_id=req.actor.company_id,
            store_id=store, request=req, scope=scope(store),
        )  # fmt: skip

    result = StepResult(StepOutcome.COMPLETED, output=report)
    good = DailyReportInput(business_date=date(2026, 3, 3))
    assert run(handler.verify(context(SOUTH), good, {}, result)) is True
    assert run(handler.verify(context(NORTH), good, {}, result)) is False
    other_day = DailyReportInput(business_date=date(2026, 3, 4))
    assert run(handler.verify(context(SOUTH), other_day, {}, result)) is False
    assert (
        run(
            handler.verify(
                context(SOUTH), good, {}, StepResult(StepOutcome.COMPLETED, output={"x": 1})
            )
        )
        is False
    )
    assert handler.report_service is not None and service.engine.registry is not None


def test_the_daily_input_is_a_date_only() -> None:
    assert DailyReportInput().business_date is None
    with pytest.raises(ValueError):
        DailyReportInput(business_date=datetime(2026, 3, 3))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DailyReportInput(store_id=SOUTH)  # type: ignore[call-arg]
