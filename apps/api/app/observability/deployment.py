"""Deployment observability runtime (Task 039): logging and OPTIONAL OTLP export.

    build_deployment_observability(settings)
        -> Product logger: ``app.product.observability`` at ``APP_LOG_LEVEL``, one stdout
           handler, the JSON completion body unchanged, no propagation (the root logger
           is never reconfigured)
        -> APP_OTEL_EXPORT_MODE=disabled (default): the global no-op OpenTelemetry API
           providers. No SDK provider, exporter, thread or network connection is created.
        -> APP_OTEL_EXPORT_MODE=otlp_http: ONE SDK TracerProvider (batch span processor)
           and ONE MeterProvider (periodic reader), each with an OTLP/HTTP exporter to the
           standard ``/v1/traces`` and ``/v1/metrics`` under the configured collector BASE
           URL. The providers are passed explicitly to the Product observability; no
           global provider is installed, so nothing else (Agno included) exports.
        -> ONE ``OpenTelemetryObservability`` for the whole application.

Only ``APP_OTEL_*`` configures export: with export enabled, ANY ``OTEL_EXPORTER_OTLP`` or
``OTEL_EXPORTER_OTLP_*`` variable in the process environment (headers, endpoints,
certificates, compression, timeouts, ...) is refused at startup with a fixed, value-free
error, before any exporter exists, so the SDK can never inherit hidden configuration.

The exported data is exactly the existing bounded Product observations (fixed operation,
outcome and low-cardinality attributes; ``request_id`` only on spans). The resource
carries only ``service.name``, ``service.version`` and the deployment environment. No
exporter header or credential is configured by the Product.

Export is best-effort: an unreachable collector never fails an operation, readiness or
System Status. ``shutdown`` flushes and stops the providers ONCE, bounded in time; a
failure while doing so is swallowed and never blocks the rest of application shutdown.
"""

import logging
import os
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TextIO

from app import __version__
from app.config import Settings
from app.observability.otel import OpenTelemetryObservability
from app.observability.structured_logs import OBSERVABILITY_LOGGER_NAME

SERVICE_NAME = "agento"
TRACES_PATH = "/v1/traces"
METRICS_PATH = "/v1/metrics"
EXPORT_TIMEOUT_SECONDS = 5.0
METRIC_EXPORT_INTERVAL_MILLIS = 30_000
SHUTDOWN_TIMEOUT_SECONDS = 10.0
_HANDLER_MARK = "_agento_product_observability_handler"
# The OpenTelemetry OTLP exporter reads its own environment (headers, endpoints,
# certificates, compression, timeouts, ...). None of it may shape the Product exporter.
_EXPORTER_ENVIRONMENT = "OTEL_EXPORTER_OTLP"
EXPORTER_ENVIRONMENT_REFUSED = (
    "OpenTelemetry exporter environment overrides are not allowed; configure Product "
    "telemetry with APP_OTEL_* settings only."
)


class TelemetryConfigurationError(RuntimeError):
    """A fixed, value-free startup error (never a variable name, value or endpoint)."""

    def __init__(self) -> None:
        super().__init__(EXPORTER_ENVIRONMENT_REFUSED)


def has_exporter_environment_override(environ: Mapping[str, str]) -> bool:
    """True if any ``OTEL_EXPORTER_OTLP`` / ``OTEL_EXPORTER_OTLP_*`` variable is set."""
    return any(
        name == _EXPORTER_ENVIRONMENT or name.startswith(f"{_EXPORTER_ENVIRONMENT}_")
        for name in environ
    )


def configure_product_logging(level: str, *, stream: TextIO | None = None) -> logging.Logger:
    """Configure ONLY the Product observability logger (idempotent: one Product handler).

    Records are written as their message (the JSON completion body) to ``stream``
    (default stdout). Propagation is off, so nothing is duplicated through the root
    logger, which is never touched."""
    logger = logging.getLogger(OBSERVABILITY_LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def otlp_signal_urls(base: str) -> tuple[str, str]:
    """The fixed OTLP/HTTP signal URLs under a validated collector base URL."""
    root = base.rstrip("/")
    return f"{root}{TRACES_PATH}", f"{root}{METRICS_PATH}"


def _bounded(call: Callable[[], object], timeout_seconds: float) -> None:
    """Run ``call`` in a daemon thread and wait at most ``timeout_seconds``; any
    exception is swallowed (telemetry never blocks or fails Product shutdown)."""

    def run() -> None:
        try:
            call()
        except Exception:  # noqa: BLE001, S110 - best-effort telemetry shutdown
            pass

    worker = threading.Thread(target=run, name="agento-telemetry-shutdown", daemon=True)
    worker.start()
    worker.join(timeout_seconds)


@dataclass
class ObservabilityRuntime:
    observability: OpenTelemetryObservability
    export_mode: str
    _shutdowns: tuple[Callable[[], object], ...] = ()
    _closed: bool = field(default=False, init=False)

    def shutdown(self, timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """Flush and stop the export providers once (idempotent, bounded, never raises)."""
        if self._closed:
            return
        self._closed = True
        for stop in self._shutdowns:
            _bounded(stop, timeout_seconds)


def build_deployment_observability(
    settings: Settings,
    *,
    stream: TextIO | None = None,
    metric_export_interval_millis: int = METRIC_EXPORT_INTERVAL_MILLIS,
    environ: Mapping[str, str] | None = None,
) -> ObservabilityRuntime:
    if settings.otel_export_mode == "otlp_http" and has_exporter_environment_override(
        os.environ if environ is None else environ
    ):
        # Fail closed BEFORE any exporter exists: only APP_OTEL_* configures Product export.
        raise TelemetryConfigurationError()
    logger = configure_product_logging(settings.log_level, stream=stream)
    if settings.otel_export_mode != "otlp_http" or settings.otel_export_endpoint is None:
        # Disabled: no exporter is ever constructed, so OTEL_EXPORTER_OTLP* has no effect.
        return ObservabilityRuntime(OpenTelemetryObservability(logger=logger), "disabled")

    # Imported only when export is explicitly enabled.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    traces_url, metrics_url = otlp_signal_urls(settings.otel_export_endpoint)
    # Built explicitly (no resource detectors, no OTEL_RESOURCE_ATTRIBUTES merge): only
    # these three low-cardinality attributes; never a host, company, store or key id.
    resource = Resource(
        {
            "service.name": SERVICE_NAME,
            "service.version": __version__,
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url, timeout=EXPORT_TIMEOUT_SECONDS))
    )
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_url, timeout=EXPORT_TIMEOUT_SECONDS),
        export_interval_millis=metric_export_interval_millis,
        export_timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000,
    )
    meter_provider = MeterProvider(
        metric_readers=[reader], resource=resource, shutdown_on_exit=False
    )
    observability = OpenTelemetryObservability(
        tracer_provider=tracer_provider, meter_provider=meter_provider, logger=logger
    )
    return ObservabilityRuntime(
        observability,
        "otlp_http",
        (
            tracer_provider.shutdown,
            lambda: meter_provider.shutdown(timeout_millis=EXPORT_TIMEOUT_SECONDS * 1000),
        ),
    )
