"""The default Product observability: structured logs + OpenTelemetry API.

    Product operation -> one span        (Product instrumentation scope)
                      -> product.operation.count     (counter)
                      -> product.operation.duration  (histogram, seconds)
                      -> one JSON completion log     (stdlib logging)

Only the OpenTelemetry API is used. Nothing here installs an SDK, exporter or global
provider, opens a connection or starts a thread: unless a deployment configures an
OpenTelemetry SDK/exporter itself, traces and metrics are no-ops and nothing leaves
the installation. Tests pass SDK providers with in-memory readers explicitly.

Metric attributes are the bounded observation attributes plus ``operation`` and
``outcome``: never ``request_id`` (metrics stay low-cardinality). Spans may carry the
server-generated ``request_id``. Exceptions are never recorded on spans (their text
could contain secrets): a failed operation only gets the generic ERROR status.

Every OpenTelemetry and logging call is individually guarded: an observability
failure is swallowed and never reaches the observed operation.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal
from uuid import UUID

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, MeterProvider
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, TracerProvider

from app import __version__
from app.observability.contracts import (
    ObservationDetails,
    ObservationOutcome,
    OperationObservation,
    ProductOperation,
)
from app.observability.structured_logs import completion_record, default_logger

INSTRUMENTATION_SCOPE = "commerce-ai-platform.product"
OPERATION_COUNT_METRIC = "product.operation.count"
OPERATION_DURATION_METRIC = "product.operation.duration"
SPAN_NAMES: dict[ProductOperation, str] = {
    operation: f"product.{operation.value}" for operation in ProductOperation
}
# Outcomes that mark the span with the generic ERROR status (no description).
_ERROR_OUTCOMES = frozenset({ObservationOutcome.ERROR, ObservationOutcome.UNAVAILABLE})

MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OpenTelemetryObservability:
    """``ProductObservability`` over the OpenTelemetry API and stdlib logging.

    ``tracer_provider``/``meter_provider`` default to the global API providers (no-op
    unless the deployment configured an SDK). ``monotonic`` measures durations;
    ``wall_clock`` stamps completion logs (aware UTC).
    """

    def __init__(
        self,
        *,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
        logger: logging.Logger | None = None,
        monotonic: MonotonicClock = time.perf_counter,
        wall_clock: WallClock = _utc_now,
    ) -> None:
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._logger = logger if logger is not None else default_logger()
        self._tracer: trace.Tracer | None = None
        self._counter: Counter | None = None
        self._histogram: Histogram | None = None
        try:
            provider = (
                tracer_provider if tracer_provider is not None else trace.get_tracer_provider()
            )
            self._tracer = provider.get_tracer(INSTRUMENTATION_SCOPE, __version__)
        except Exception:  # noqa: BLE001, S110 - observability never affects the Product
            pass
        try:
            mp = meter_provider if meter_provider is not None else metrics.get_meter_provider()
            meter = mp.get_meter(INSTRUMENTATION_SCOPE, __version__)
            self._counter = meter.create_counter(
                OPERATION_COUNT_METRIC,
                unit="{operation}",
                description="Completed Product operations by operation and outcome.",
            )
            self._histogram = meter.create_histogram(
                OPERATION_DURATION_METRIC,
                unit="s",
                description="Duration of Product operations in seconds.",
            )
        except Exception:  # noqa: BLE001, S110 - observability never affects the Product
            pass

    def operation(
        self, operation: ProductOperation, *, request_id: UUID | None = None
    ) -> "_OperationScope":
        return _OperationScope(self, operation, request_id)


class _OperationScope:
    def __init__(
        self,
        owner: OpenTelemetryObservability,
        operation: ProductOperation,
        request_id: UUID | None,
    ) -> None:
        self._owner = owner
        self._operation = ProductOperation(operation)
        self._request_id = str(request_id) if isinstance(request_id, UUID) else None
        self._start = 0.0
        self._span: Span | None = None
        self._token: object | None = None
        self._finished = False

    # -- lifecycle ---------------------------------------------------------------------

    def __enter__(self) -> OperationObservation:
        owner = self._owner
        try:
            self._start = owner._monotonic()
        except Exception:  # noqa: BLE001 - observability never affects the Product
            self._start = 0.0
        tracer = owner._tracer
        if tracer is not None:
            try:
                attributes: dict[str, str] = {"operation": self._operation.value}
                if self._request_id is not None:
                    attributes["request_id"] = self._request_id
                kind = (
                    SpanKind.SERVER
                    if self._operation is ProductOperation.HTTP_REQUEST
                    else SpanKind.INTERNAL
                )
                self._span = tracer.start_span(
                    SPAN_NAMES[self._operation], kind=kind, attributes=attributes
                )
                # The Product span is current for the operation: nested Product
                # operations (a service inside an HTTP request) become its children.
                self._token = otel_context.attach(trace.set_span_in_context(self._span))
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        try:
            if not self._finished:
                # Left without an explicit outcome (an exception, or a forgotten
                # finish): a generic failure; the exception itself is never recorded.
                self.finish(ObservationOutcome.ERROR)
        finally:
            token, self._token = self._token, None
            if token is not None:
                try:
                    otel_context.detach(token)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                    pass
        return False

    # -- outcome -----------------------------------------------------------------------

    def finish(
        self, outcome: ObservationOutcome, details: ObservationDetails | None = None
    ) -> None:
        if self._finished:
            return  # idempotent: the first outcome wins
        self._finished = True
        owner = self._owner
        try:
            outcome = ObservationOutcome(outcome)
        except Exception:  # noqa: BLE001 - an invalid outcome is recorded as a generic error
            outcome = ObservationOutcome.ERROR
        try:
            attributes = details.attributes() if details is not None else {}
        except Exception:  # noqa: BLE001 - invalid details are dropped, never guessed
            attributes = {}
        try:
            duration = max(0.0, owner._monotonic() - self._start)
        except Exception:  # noqa: BLE001 - observability never affects the Product
            duration = 0.0

        trace_id = span_id = None
        span = self._span
        if span is not None:
            try:
                span.set_attributes({"outcome": outcome.value, **attributes})
                if outcome in _ERROR_OUTCOMES:
                    span.set_status(Status(StatusCode.ERROR))
                span_context = span.get_span_context()
                if span_context.is_valid:
                    trace_id = format(span_context.trace_id, "032x")
                    span_id = format(span_context.span_id, "016x")
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass
            try:
                span.end()
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass

        # Metrics: bounded attributes only; request_id is never a metric attribute.
        metric_attributes: dict[str, str | int | bool] = {
            "operation": self._operation.value,
            "outcome": outcome.value,
        }
        metric_attributes.update(attributes)
        if owner._counter is not None:
            try:
                owner._counter.add(1, metric_attributes)
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass
        if owner._histogram is not None:
            try:
                owner._histogram.record(duration, metric_attributes)
            except Exception:  # noqa: BLE001, S110 - observability never affects the Product
                pass

        try:
            timestamp = owner._wall_clock()
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
                timestamp = None
            else:
                timestamp = timestamp.astimezone(UTC)
        except Exception:  # noqa: BLE001 - observability never affects the Product
            timestamp = None
        try:
            owner._logger.info(
                completion_record(
                    operation=self._operation.value,
                    outcome=outcome.value,
                    duration_ms=round(duration * 1000, 3),
                    timestamp=timestamp,
                    request_id=self._request_id,
                    trace_id=trace_id,
                    span_id=span_id,
                    attributes=attributes,
                )
            )
        except Exception:  # noqa: BLE001, S110 - observability never affects the Product
            pass


def build_default_observability() -> OpenTelemetryObservability:
    """The Product default: global OpenTelemetry API providers (no-op unless the
    deployment configured an SDK) and the Product observability logger. No network,
    database, backend, integration or model dependency; no background thread."""
    return OpenTelemetryObservability()
