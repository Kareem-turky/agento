"""TEST-ONLY observability doubles and an in-memory OpenTelemetry SDK harness.

Never used by production code. The SDK providers here are passed explicitly to the
Product implementation; nothing sets a global provider and no exporter is involved.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext

from app.observability import (
    ObservationDetails,
    ObservationOutcome,
    OpenTelemetryObservability,
    ProductOperation,
)

FIXED_NOW = datetime(2026, 3, 3, 12, 0, 0, tzinfo=UTC)


# ----- Recording / failing ProductObservability -------------------------------------------


@dataclass
class Recorded:
    operation: ProductOperation
    request_id: UUID | None
    outcomes: list[tuple[ObservationOutcome, ObservationDetails | None]] = field(
        default_factory=list
    )
    exited_with: type[BaseException] | None = None
    exited: bool = False

    @property
    def outcome(self) -> ObservationOutcome | None:
        return self.outcomes[0][0] if self.outcomes else None

    @property
    def attributes(self) -> dict[str, Any]:
        details = self.outcomes[0][1] if self.outcomes else None
        return details.attributes() if details is not None else {}


class _RecordingScope:
    def __init__(self, record: Recorded) -> None:
        self.record = record

    def __enter__(self):
        return self

    def finish(self, outcome, details=None) -> None:
        self.record.outcomes.append((outcome, details))

    def __exit__(self, exc_type, exc, tb: TracebackType | None) -> None:
        self.record.exited = True
        self.record.exited_with = exc_type


class RecordingObservability:
    def __init__(self) -> None:
        self.records: list[Recorded] = []

    def operation(self, operation, *, request_id=None):
        record = Recorded(operation, request_id)
        self.records.append(record)
        return _RecordingScope(record)

    def of(self, operation: ProductOperation) -> list[Recorded]:
        return [r for r in self.records if r.operation is operation]


class ObservabilityFailure(Exception):
    """Raised by the failing doubles; must never reach a Product caller."""


class FailingObservability:
    """Fails at a chosen point: ``operation`` (start), ``enter``, ``finish`` or ``exit``.
    ``exit`` may also try to SUPPRESS the operation's exception (returns True)."""

    def __init__(self, where: str) -> None:
        self.where = where
        self.calls = 0

    def operation(self, operation, *, request_id=None):
        self.calls += 1
        if self.where == "operation":
            raise ObservabilityFailure("OBS-EXCEPTION-start")
        return _FailingScope(self.where)


class _FailingScope:
    def __init__(self, where: str) -> None:
        self.where = where

    def __enter__(self):
        if self.where == "enter":
            raise ObservabilityFailure("OBS-EXCEPTION-enter")
        return self

    def finish(self, outcome, details=None) -> None:
        if self.where == "finish":
            raise ObservabilityFailure("OBS-EXCEPTION-finish")

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.where == "exit":
            raise ObservabilityFailure("OBS-EXCEPTION-exit")
        return self.where == "suppress"  # an implementation trying to swallow errors


FAILURE_POINTS = ("operation", "enter", "finish", "exit", "suppress")


# ----- In-memory OpenTelemetry SDK harness --------------------------------------------------


def attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def ctx(span: ReadableSpan) -> SpanContext:
    context = span.context
    assert context is not None
    return context


def parent(span: ReadableSpan) -> SpanContext:
    assert span.parent is not None
    return span.parent


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class SteppingClock:
    """Monotonic test clock: each call advances by ``step`` seconds."""

    def __init__(self, start: float = 100.0, step: float = 0.25) -> None:
        self.now, self.step = start, step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


@dataclass
class Harness:
    observability: OpenTelemetryObservability
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    handler: ListHandler

    def finished_spans(self) -> list[ReadableSpan]:
        return list(self.spans.get_finished_spans())

    def logs(self) -> list[dict[str, Any]]:
        return [json.loads(m) for m in self.handler.messages]

    def metric_points(self) -> dict[str, list[tuple[dict[str, Any], Any]]]:
        """metric name -> [(attributes, point)] from the in-memory reader."""
        data = self.metrics.get_metrics_data()
        out: dict[str, list[tuple[dict[str, Any], Any]]] = {}
        if data is None:
            return out
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    for point in metric.data.data_points:
                        attributes: dict[str, Any] = dict(point.attributes or {})
                        out.setdefault(metric.name, []).append((attributes, point))
        return out

    def metric_scopes(self) -> set[str]:
        data = self.metrics.get_metrics_data()
        if data is None:
            return set()
        return {sm.scope.name for rm in data.resource_metrics for sm in rm.scope_metrics}


_logger_serial = 0


def harness(monotonic=None, wall_clock=lambda: FIXED_NOW, *, product_logger: bool = False
            ) -> Harness:  # fmt: skip
    """A Product observability wired to in-memory SDK providers and a private logger
    (or, with ``product_logger``, the real Product logger: capture it with caplog)."""
    global _logger_serial
    _logger_serial += 1
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    logger = logging.getLogger(f"tests.observability.harness.{_logger_serial}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = ListHandler()
    logger.addHandler(handler)
    observability = OpenTelemetryObservability(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger=None if product_logger else logger,
        monotonic=monotonic or SteppingClock(),
        wall_clock=wall_clock,
    )
    return Harness(observability, spans, reader, handler)
