"""Observed Product service decorators: transparent, bounded, failure-isolated."""

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.context.models import RequestContext
from app.governance import ActionScope
from app.observability import ObservationOutcome, ProductOperation
from app.observability.services import (
    ObservedDailyOperationsReportService,
    ObservedOperationsRunService,
    ObservedOperationsTicketCommandQueryService,
    ObservedOperationsTicketCommandService,
    observed_daily_operations_service,
    observed_operations_service,
    observed_ticket_query_service,
    observed_ticket_service,
)
from app.services.operations import OperationsRunService, ProductOperationsRunResult
from app.services.operations_reports import (
    DailyOperationsForbiddenError,
    DailyOperationsReportService,
    DailyOperationsUnavailableError,
)
from app.services.operations_tickets import (
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
    ProductTicketCommandResult,
    ProductTicketCommandStatusResult,
    TicketCommandNotFoundError,
    TicketCommandQueryUnavailableError,
    TicketCommandReason,
    TicketCommandStatus,
    TicketCommandUnavailableError,
)
from tests.support.observability import (
    FAILURE_POINTS,
    FailingObservability,
    RecordingObservability,
    attrs,
    harness,
)

Out, P = ObservationOutcome, ProductOperation
S, R = TicketCommandStatus, TicketCommandReason
REQUEST = RequestContext()
SCOPE = ActionScope(company_id="OBS-SECRET-company", store_id="OBS-SECRET-store")
MESSAGE = "OBS-SECRET-MESSAGE-run"
TITLE, DESCRIPTION = "OBS-SECRET-TITLE", "OBS-SECRET-DESCRIPTION"
KEY = "OBS-IDEMPOTENCY-key-0001"
COMMAND, TICKET = UUID(int=0xC0FFEE), UUID(int=0x71C7E7)
NOW = datetime(2026, 3, 3, 12, tzinfo=UTC)


def ticket_result(**overrides) -> ProductTicketCommandResult:
    data = {"command_id": COMMAND, "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "replayed": False, "persistence_complete": True}  # fmt: skip
    return ProductTicketCommandResult(**(data | overrides))


def status_result(**overrides) -> ProductTicketCommandStatusResult:
    data = {"command_id": COMMAND, "status": S.VERIFIED, "reason": R.VERIFIED,
            "ticket_id": TICKET, "created_at": NOW, "updated_at": NOW}  # fmt: skip
    return ProductTicketCommandStatusResult(**(data | overrides))


class Fake:
    """Returns or raises exactly what it is told; records every call."""

    def __init__(self, returns=None, raises: BaseException | None = None) -> None:
        self.returns, self.raises = returns, raises
        self.calls: list[tuple] = []

    async def _answer(self, *args) -> Any:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.returns


class FakeRun(Fake):
    async def run_product(self, request, scope, message) -> Any:
        return await self._answer(request, scope, message)


class FakeReport(Fake):
    async def get_daily_report(self, request, scope, business_date) -> Any:
        return await self._answer(request, scope, business_date)


class FakeTicket(Fake):
    async def create_ticket(self, request, scope, title, description, idempotency_key) -> Any:
        return await self._answer(request, scope, title, description, idempotency_key)


class FakeQuery(Fake):
    async def get_command(self, request, command_id) -> Any:
        return await self._answer(request, command_id)


def run(coro):
    return asyncio.run(coro)


# ----- Transparency ------------------------------------------------------------------------


def test_wrappers_implement_the_same_protocols() -> None:
    obs = RecordingObservability()
    assert isinstance(ObservedOperationsRunService(FakeRun(), obs), OperationsRunService)
    assert isinstance(ObservedDailyOperationsReportService(FakeReport(), obs),
                      DailyOperationsReportService)  # fmt: skip
    assert isinstance(ObservedOperationsTicketCommandService(FakeTicket(), obs),
                      OperationsTicketCommandService)  # fmt: skip
    assert isinstance(ObservedOperationsTicketCommandQueryService(FakeQuery(), obs),
                      OperationsTicketCommandQueryService)  # fmt: skip


def test_slot_helpers_keep_none_and_foreign_objects_untouched() -> None:
    obs = RecordingObservability()
    for helper in (observed_operations_service, observed_daily_operations_service,
                   observed_ticket_service, observed_ticket_query_service):  # fmt: skip
        assert helper(None, obs) is None
        foreign = object()
        assert helper(foreign, obs) is foreign  # type: ignore[arg-type]
    service = FakeTicket()
    wrapped = observed_ticket_service(service, obs)
    assert isinstance(wrapped, ObservedOperationsTicketCommandService)
    assert wrapped.delegate is service


def test_run_service_passes_arguments_and_result_through_unchanged() -> None:
    obs = RecordingObservability()
    result = ProductOperationsRunResult(message="final text")
    fake = FakeRun(result)
    returned = run(ObservedOperationsRunService(fake, obs).run_product(REQUEST, SCOPE, MESSAGE))
    assert returned is result and fake.calls == [(REQUEST, SCOPE, MESSAGE)]
    (record,) = obs.of(P.OPERATIONS_AGENT_RUN)
    assert record.request_id == REQUEST.request_id
    assert record.outcome is Out.COMPLETED and record.attributes == {}


def test_run_service_failure_is_error_and_the_same_exception() -> None:
    obs = RecordingObservability()
    error = RuntimeError("OBS-EXCEPTION-model-failure")
    with pytest.raises(RuntimeError) as caught:
        run(ObservedOperationsRunService(FakeRun(raises=error), obs)
            .run_product(REQUEST, SCOPE, MESSAGE))  # fmt: skip
    assert caught.value is error
    (record,) = obs.records
    assert record.outcome is None and record.exited_with is RuntimeError  # generic error


@pytest.mark.parametrize(
    ("raises", "outcome"),
    [(None, Out.COMPLETED), (DailyOperationsForbiddenError(), Out.DENIED),
     (DailyOperationsUnavailableError(), Out.UNAVAILABLE), (ValueError("OBS-EXCEPTION"), None)],
)  # fmt: skip
def test_daily_report_outcomes(raises, outcome) -> None:
    obs = RecordingObservability()
    report = object()  # returned by identity; never inspected by the observer
    fake = FakeReport(report, raises)
    service = ObservedDailyOperationsReportService(fake, obs)
    day = date(2026, 3, 3)
    if raises is None:
        assert run(service.get_daily_report(REQUEST, SCOPE, day)) is report
    else:
        with pytest.raises(type(raises)) as caught:
            run(service.get_daily_report(REQUEST, SCOPE, day))
        assert caught.value is raises
    assert fake.calls == [(REQUEST, SCOPE, day)]
    (record,) = obs.of(P.DAILY_REPORT)
    assert record.outcome is outcome and record.attributes == {}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"replayed": True},
        {"status": S.DENIED, "reason": R.POLICY_DENIED, "ticket_id": None},
        {"status": S.FAILED, "reason": R.INPUT_INVALID, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.EXECUTION_OUTCOME_UNCERTAIN, "ticket_id": None},
        {"status": S.REQUIRES_HUMAN, "reason": R.COMMAND_PERSISTENCE_INCOMPLETE,
         "ticket_id": None, "persistence_complete": False},
        {"status": S.IN_PROGRESS, "reason": None, "ticket_id": None, "replayed": True},
        {"status": S.AWAITING_APPROVAL, "reason": R.APPROVAL_REQUIRED, "ticket_id": None},
    ],
)  # fmt: skip
def test_ticket_command_metadata_is_canonical_and_bounded(fields) -> None:
    obs = RecordingObservability()
    result = ticket_result(**fields)
    fake = FakeTicket(result)
    service = ObservedOperationsTicketCommandService(fake, obs)
    assert run(service.create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY)) is result
    assert fake.calls == [(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY)]
    (record,) = obs.of(P.TICKET_COMMAND)
    assert record.outcome is Out.COMPLETED
    expected = {"business_status": result.status.value, "replayed": result.replayed,
                "persistence_complete": result.persistence_complete}  # fmt: skip
    if result.reason is not None:
        expected["business_reason"] = result.reason.value
    assert record.attributes == expected


@pytest.mark.parametrize(
    ("raises", "outcome"),
    [(InvalidIdempotencyKeyError(), Out.INVALID), (IdempotencyConflictError(), Out.CONFLICT),
     (TicketCommandUnavailableError(), Out.UNAVAILABLE), (KeyError("OBS-EXCEPTION"), None)],
)  # fmt: skip
def test_ticket_command_errors_map_and_reraise(raises, outcome) -> None:
    obs = RecordingObservability()
    service = ObservedOperationsTicketCommandService(FakeTicket(raises=raises), obs)
    with pytest.raises(type(raises)) as caught:
        run(service.create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY))
    assert caught.value is raises
    (record,) = obs.records
    assert record.outcome is outcome and record.attributes == {}


@pytest.mark.parametrize(
    ("raises", "outcome"),
    [(None, Out.COMPLETED), (TicketCommandNotFoundError(), Out.NOT_FOUND),
     (TicketCommandQueryUnavailableError(), Out.UNAVAILABLE), (OSError("OBS-EXCEPTION"), None)],
)  # fmt: skip
def test_ticket_query_outcomes(raises, outcome) -> None:
    obs = RecordingObservability()
    result = status_result(status=S.DENIED, reason=R.POLICY_DENIED, ticket_id=None)
    fake = FakeQuery(result, raises)
    service = ObservedOperationsTicketCommandQueryService(fake, obs)
    if raises is None:
        assert run(service.get_command(REQUEST, COMMAND)) is result
    else:
        with pytest.raises(type(raises)) as caught:
            run(service.get_command(REQUEST, COMMAND))
        assert caught.value is raises
    assert fake.calls == [(REQUEST, COMMAND)]
    (record,) = obs.of(P.TICKET_COMMAND_QUERY)
    assert record.outcome is outcome
    assert record.attributes == ({"business_status": "denied", "business_reason": "policy_denied"}
                                 if raises is None else {})  # fmt: skip


def test_unexpected_result_types_are_not_observed_as_completed() -> None:
    obs = RecordingObservability()
    odd = object()
    service = ObservedOperationsTicketCommandService(FakeTicket(odd), obs)
    assert run(service.create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY)) is odd
    assert obs.records[0].outcome is None  # left to the route; generic error on exit


# ----- Failure isolation: observability can never change, fail or retry an operation ---


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_failing_observability_never_changes_the_operation(where: str) -> None:
    result = ticket_result()
    fake = FakeTicket(result)
    service = ObservedOperationsTicketCommandService(fake, FailingObservability(where))
    assert run(service.create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY)) is result
    assert len(fake.calls) == 1  # exactly once: no retry

    conflict = IdempotencyConflictError()
    failing = ObservedOperationsTicketCommandService(FakeTicket(raises=conflict),
                                                     FailingObservability(where))  # fmt: skip
    with pytest.raises(IdempotencyConflictError) as caught:
        run(failing.create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY))
    assert caught.value is conflict  # never suppressed, even by an implementation trying

    report = object()
    reports = ObservedDailyOperationsReportService(FakeReport(report), FailingObservability(where))
    assert run(reports.get_daily_report(REQUEST, SCOPE, None)) is report
    runs = ObservedOperationsRunService(FakeRun("r"), FailingObservability(where))
    assert run(runs.run_product(REQUEST, SCOPE, MESSAGE)) == "r"
    query = FakeQuery(status_result())
    queries = ObservedOperationsTicketCommandQueryService(query, FailingObservability(where))
    assert run(queries.get_command(REQUEST, COMMAND)) is query.returns
    assert len(query.calls) == 1


# ----- Data minimization through the default implementation --------------------------------


def test_no_business_payload_reaches_spans_metrics_or_logs() -> None:
    h = harness()
    obs = h.observability
    run(ObservedOperationsRunService(FakeRun(ProductOperationsRunResult(message="OBS-SECRET-reply")),
                                     obs).run_product(REQUEST, SCOPE, MESSAGE))  # fmt: skip
    run(ObservedOperationsTicketCommandService(FakeTicket(ticket_result()), obs)
        .create_ticket(REQUEST, SCOPE, TITLE, DESCRIPTION, KEY))  # fmt: skip
    run(ObservedOperationsTicketCommandQueryService(FakeQuery(status_result()), obs)
        .get_command(REQUEST, COMMAND))  # fmt: skip
    with pytest.raises(RuntimeError):
        run(ObservedDailyOperationsReportService(
            FakeReport(raises=RuntimeError("OBS-EXCEPTION-report")), obs)
            .get_daily_report(REQUEST, SCOPE, date(2031, 7, 19)))  # fmt: skip

    dumped = repr(h.logs()) + repr([attrs(s) for s in h.finished_spans()])
    dumped += repr(h.metric_points())
    forbidden = ["OBS-SECRET", "OBS-IDEMPOTENCY", "OBS-EXCEPTION", str(COMMAND), str(TICKET),
                 "2031-07-19", "business_date", "store", "company", "actor"]  # fmt: skip
    for marker in forbidden:
        assert marker not in dumped, marker
    assert str(uuid4()) not in dumped
