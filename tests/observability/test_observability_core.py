"""The Product observability contract and its default OpenTelemetry/log implementation."""

import json
import logging
import socket
import threading
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind, StatusCode

from app.observability import (
    BusinessDetails,
    HttpDetails,
    ObservationDetails,
    ObservationOutcome,
    OpenTelemetryObservability,
    ProductObservability,
    ProductOperation,
    ProductRoute,
    build_default_observability,
    observe,
)
from app.observability.otel import (
    INSTRUMENTATION_SCOPE,
    OPERATION_COUNT_METRIC,
    OPERATION_DURATION_METRIC,
)
from app.observability.structured_logs import COMPLETION_EVENT, OBSERVABILITY_LOGGER_NAME
from app.services.operations_tickets import TicketCommandReason, TicketCommandStatus
from tests.support.observability import (
    FAILURE_POINTS,
    FIXED_NOW,
    FailingObservability,
    RecordingObservability,
    SteppingClock,
    attrs,
    ctx,
    harness,
)
from tests.support.observability import parent as parent_of

Out, P = ObservationOutcome, ProductOperation
RID = UUID("11111111-2222-4333-8444-555555555555")
TICKET_DETAILS = ObservationDetails(
    business=BusinessDetails(
        status=TicketCommandStatus.VERIFIED,
        reason=TicketCommandReason.VERIFIED,
        replayed=False,
        persistence_complete=True,
    )
)


class Boom(Exception):
    pass


# ----- Vocabulary ---------------------------------------------------------------------------


def test_stable_operation_and_outcome_vocabulary() -> None:
    assert [o.value for o in ProductOperation] == [
        "http.request", "operations.agent_run", "operations.daily_report",
        "operations.ticket_command", "operations.ticket_command_query",
        # Task 034: Workflow runs and Step attempts (labels: catalog ids, statuses).
        "workflow.run", "workflow.step_attempt",
        # Task 035: Knowledge retrieval and governed Knowledge writes (labels: enums only).
        "knowledge.query", "knowledge.mutation",
        # Task 036: approval request / decision / consumption (labels: enums only).
        "approval.request", "approval.decision", "approval.consume",
        # Task 037: conversation ingest / read / delivery (labels: enums only).
        "conversation.ingest", "conversation.read", "conversation.delivery",
        # Task 039: the Product-authenticated System Status read (label: overall status).
        "system.status",
    ]  # fmt: skip
    assert [o.value for o in ObservationOutcome] == [
        "completed", "denied", "invalid", "conflict", "not_found", "unavailable", "error",
    ]  # fmt: skip
    assert [r.value for r in ProductRoute] == [
        "/health", "/api/v1/operations/runs", "/api/v1/operations/reports/daily",
        "/api/v1/operations/tickets", "/api/v1/operations/tickets/commands",
        # Task 039: public liveness/readiness and System Status (fixed paths only).
        "/health/live", "/health/ready", "/api/v1/system/status",
    ]  # fmt: skip


def test_details_are_bounded_by_construction() -> None:
    with pytest.raises(ValueError):
        HttpDetails(method="BREW", route=ProductRoute.HEALTH, status_code=200)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        HttpDetails(method="GET", route="/raw/path", status_code=200)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        HttpDetails(method="GET", route=ProductRoute.HEALTH, status_code=999)
    with pytest.raises(ValueError):
        BusinessDetails(status="free text")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BusinessDetails(replayed="yes")  # type: ignore[arg-type]
    assert TICKET_DETAILS.attributes() == {
        "business_status": "verified", "business_reason": "verified",
        "replayed": False, "persistence_complete": True,
    }  # fmt: skip
    http = ObservationDetails(http=HttpDetails("POST", ProductRoute.OPERATIONS_TICKETS, 201))
    assert http.attributes() == {"http.method": "POST",
                                 "http.route": "/api/v1/operations/tickets",
                                 "http.status_code": 201}  # fmt: skip


def test_default_implementation_satisfies_the_contract() -> None:
    assert isinstance(build_default_observability(), ProductObservability)
    assert isinstance(RecordingObservability(), ProductObservability)


# ----- observe(): failure isolation --------------------------------------------------------


@pytest.mark.parametrize("where", FAILURE_POINTS)
def test_observe_never_raises_and_never_suppresses(where: str) -> None:
    observer = FailingObservability(where)
    with observe(observer, P.DAILY_REPORT, RID) as observation:
        observation.finish(Out.COMPLETED)
        value = "business result"
    assert value == "business result"

    error = Boom("the operation's own failure")
    with pytest.raises(Boom) as caught:
        with observe(observer, P.DAILY_REPORT, RID) as observation:
            raise error
    assert caught.value is error  # the same exception object, unchanged


def test_observe_with_no_observability_is_inert() -> None:
    with observe(None, P.HTTP_REQUEST) as observation:
        observation.finish(Out.COMPLETED)


def test_observe_passes_only_uuid_request_ids() -> None:
    recorder = RecordingObservability()
    with observe(recorder, P.HTTP_REQUEST, "attacker-controlled"):
        pass
    with observe(recorder, P.HTTP_REQUEST, RID):
        pass
    assert [r.request_id for r in recorder.records] == [None, RID]


# ----- Default implementation: spans, metrics, logs ----------------------------------------


def test_completed_operation_produces_one_span_metrics_and_one_log() -> None:
    h = harness(monotonic=SteppingClock(start=10.0, step=0.5))
    with h.observability.operation(P.TICKET_COMMAND, request_id=RID) as observation:
        observation.finish(Out.COMPLETED, TICKET_DETAILS)

    (span,) = h.finished_spans()
    assert span.name == "product.operations.ticket_command"
    assert span.instrumentation_scope is not None
    assert span.instrumentation_scope.name == INSTRUMENTATION_SCOPE
    assert span.kind is SpanKind.INTERNAL
    assert attrs(span) == {
        "operation": "operations.ticket_command", "request_id": str(RID),
        "outcome": "completed", "business_status": "verified", "business_reason": "verified",
        "replayed": False, "persistence_complete": True,
    }  # fmt: skip
    assert span.status.status_code is StatusCode.UNSET

    points = h.metric_points()
    assert h.metric_scopes() == {INSTRUMENTATION_SCOPE}
    expected = {"operation": "operations.ticket_command", "outcome": "completed",
                "business_status": "verified", "business_reason": "verified",
                "replayed": False, "persistence_complete": True}  # fmt: skip
    ((count_attrs, count),) = points[OPERATION_COUNT_METRIC]
    assert count_attrs == expected and count.value == 1
    ((duration_attrs, duration),) = points[OPERATION_DURATION_METRIC]
    assert duration_attrs == expected
    assert duration.count == 1 and duration.sum == pytest.approx(0.5)  # seconds

    (log,) = h.logs()
    context = ctx(span)
    assert log == {
        "event": COMPLETION_EVENT, "operation": "operations.ticket_command",
        "outcome": "completed", "duration_ms": 500.0, "timestamp": FIXED_NOW.isoformat(),
        "request_id": str(RID), "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"), "business_status": "verified",
        "business_reason": "verified", "replayed": False, "persistence_complete": True,
    }  # fmt: skip


def test_log_body_is_deterministic_sorted_compact_json() -> None:
    h = harness()
    for _ in range(2):
        with h.observability.operation(P.DAILY_REPORT, request_id=RID) as observation:
            observation.finish(Out.DENIED)
    first, second = h.handler.messages
    parsed = json.loads(first)
    assert first == json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    strip = {"trace_id", "span_id"}
    assert {k: v for k, v in json.loads(first).items() if k not in strip} == {
        k: v for k, v in json.loads(second).items() if k not in strip
    }


def test_timestamp_is_aware_utc_from_the_injected_wall_clock() -> None:
    cairo = timezone(timedelta(hours=2))
    h = harness(wall_clock=lambda: datetime(2026, 3, 3, 14, 0, tzinfo=cairo))
    with h.observability.operation(P.DAILY_REPORT) as observation:
        observation.finish(Out.COMPLETED)
    assert h.logs()[0]["timestamp"] == "2026-03-03T12:00:00+00:00"

    naive = harness(wall_clock=lambda: datetime(2026, 3, 3, 12, 0))  # noqa: DTZ001
    with naive.observability.operation(P.DAILY_REPORT) as observation:
        observation.finish(Out.COMPLETED)
    assert naive.logs()[0]["timestamp"] is None  # never a guessed timezone


def test_duration_is_monotonic_and_never_negative() -> None:
    h = harness(monotonic=SteppingClock(start=50.0, step=-3.0))  # a clock going backwards
    with h.observability.operation(P.DAILY_REPORT) as observation:
        observation.finish(Out.COMPLETED)
    assert h.logs()[0]["duration_ms"] == 0.0
    ((_, point),) = h.metric_points()[OPERATION_DURATION_METRIC]
    assert point.sum == 0.0


def test_finish_is_idempotent_first_outcome_wins() -> None:
    h = harness()
    with h.observability.operation(P.TICKET_COMMAND_QUERY) as observation:
        observation.finish(Out.NOT_FOUND)
        observation.finish(Out.COMPLETED)
    assert [log["outcome"] for log in h.logs()] == ["not_found"]
    assert len(h.finished_spans()) == 1
    ((_, count),) = h.metric_points()[OPERATION_COUNT_METRIC]
    assert count.value == 1


def test_exit_without_finish_records_a_generic_error_without_exception_data() -> None:
    h = harness()
    with pytest.raises(Boom):
        with h.observability.operation(P.OPERATIONS_AGENT_RUN, request_id=RID):
            raise Boom("OBS-EXCEPTION-secret-in-message")
    with h.observability.operation(P.OPERATIONS_AGENT_RUN):
        pass  # forgot to finish

    first, second = h.finished_spans()
    for span in (first, second):
        assert attrs(span)["outcome"] == "error"
        assert span.status.status_code is StatusCode.ERROR
        assert span.status.description is None
        assert list(span.events) == []  # no record_exception
    assert [log["outcome"] for log in h.logs()] == ["error", "error"]
    assert "OBS-EXCEPTION" not in json.dumps(h.logs())


@pytest.mark.parametrize(
    ("outcome", "error_status"),
    [(Out.COMPLETED, False), (Out.DENIED, False), (Out.INVALID, False), (Out.CONFLICT, False),
     (Out.NOT_FOUND, False), (Out.UNAVAILABLE, True), (Out.ERROR, True)],
)  # fmt: skip
def test_span_status_is_error_only_for_failures(outcome, error_status) -> None:
    h = harness()
    with h.observability.operation(P.DAILY_REPORT) as observation:
        observation.finish(outcome)
    (span,) = h.finished_spans()
    assert (span.status.status_code is StatusCode.ERROR) is error_status


def test_request_id_is_a_span_and_log_field_but_never_a_metric_attribute() -> None:
    h = harness()
    for operation in ProductOperation:
        with h.observability.operation(operation, request_id=uuid4()) as observation:
            observation.finish(Out.COMPLETED)
    for span in h.finished_spans():
        UUID(attrs(span)["request_id"])
    assert all(UUID(log["request_id"]) for log in h.logs())
    for name, points in h.metric_points().items():
        for attributes, _ in points:
            assert "request_id" not in attributes, name
            assert set(attributes) <= {"operation", "outcome"}


def test_nested_operation_span_is_a_child_of_the_current_product_span() -> None:
    h = harness()
    with h.observability.operation(P.HTTP_REQUEST, request_id=RID) as outer:
        with h.observability.operation(P.TICKET_COMMAND, request_id=RID) as inner:
            inner.finish(Out.COMPLETED)
        outer.finish(Out.COMPLETED)
    child, parent = h.finished_spans()
    assert parent.kind is SpanKind.SERVER and child.kind is SpanKind.INTERNAL
    assert parent_of(child).span_id == ctx(parent).span_id
    assert ctx(child).trace_id == ctx(parent).trace_id
    assert ctx(child).span_id != ctx(parent).span_id
    assert trace.get_current_span().get_span_context().is_valid is False  # context restored


# ----- Default construction: no SDK, no network, no thread, no global mutation -------------


def test_default_observability_without_sdk_is_safe_and_local(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    connections: list[object] = []

    def refuse(*args, **kwargs):
        connections.append(args)
        raise AssertionError("observability must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    tracer_provider, meter_provider = trace.get_tracer_provider(), metrics.get_meter_provider()
    threads = threading.active_count()

    caplog.set_level(logging.INFO, logger=OBSERVABILITY_LOGGER_NAME)
    observability = build_default_observability()
    with observability.operation(P.HTTP_REQUEST, request_id=RID) as observation:
        observation.finish(Out.COMPLETED)

    assert connections == []
    assert threading.active_count() == threads
    assert trace.get_tracer_provider() is tracer_provider  # nothing installed globally
    assert metrics.get_meter_provider() is meter_provider
    (record,) = [r for r in caplog.records if r.name == OBSERVABILITY_LOGGER_NAME]
    log = json.loads(record.getMessage())
    assert log["request_id"] == str(RID) and log["outcome"] == "completed"
    # No SDK configured: no valid span context, so no (fake) trace or span id.
    assert log["trace_id"] is None and log["span_id"] is None


def test_default_observability_does_not_touch_root_logging() -> None:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    build_default_observability()
    assert root.handlers == handlers and root.level == level


# ----- Failing tracer / meter / logger inside the default implementation -------------------


class _Exploding:
    def __getattr__(self, name):
        raise RuntimeError("OBS-EXCEPTION-backend")


class ExplodingTracerProvider:
    def get_tracer(self, *args, **kwargs):
        return _Exploding()


class ExplodingMeterProvider:
    def get_meter(self, *args, **kwargs):
        return _Exploding()


class BrokenInstrumentsMeterProvider:
    class _Meter:
        def create_counter(self, *a, **k):
            return _Exploding()

        def create_histogram(self, *a, **k):
            return _Exploding()

    def get_meter(self, *args, **kwargs):
        return self._Meter()


class ExplodingLogger(logging.Logger):
    def info(self, *args, **kwargs):
        raise RuntimeError("OBS-EXCEPTION-logger")


def _explode(*args, **kwargs):
    raise RuntimeError("OBS-EXCEPTION-provider")


class RaisingProvider:
    get_tracer = get_meter = staticmethod(_explode)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tracer_provider": ExplodingTracerProvider()},
        {"tracer_provider": RaisingProvider()},
        {"meter_provider": ExplodingMeterProvider()},
        {"meter_provider": RaisingProvider()},
        {"meter_provider": BrokenInstrumentsMeterProvider()},
        {"logger": ExplodingLogger("broken")},
        {"monotonic": _explode, "wall_clock": _explode},
    ],
    ids=["tracer", "tracer-provider", "meter", "meter-provider", "instruments", "logger",
         "clocks"],
)  # fmt: skip
def test_failing_backends_never_raise(kwargs) -> None:
    observability = OpenTelemetryObservability(**kwargs)
    with observability.operation(P.TICKET_COMMAND, request_id=RID) as observation:
        observation.finish(Out.COMPLETED, TICKET_DETAILS)
    with pytest.raises(Boom):
        with observability.operation(P.TICKET_COMMAND, request_id=RID):
            raise Boom
    assert datetime.now(UTC)  # still running normally
