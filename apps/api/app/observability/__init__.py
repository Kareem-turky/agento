"""Product-owned observability (operational health, not audit).

Business and application code depends only on ``app.observability.contracts``;
OpenTelemetry is imported only inside this package (``app.observability.otel``).
Nothing here exports telemetry over the network: a deployment may attach an
OpenTelemetry SDK/exporter later.
"""

from app.observability.contracts import (
    BusinessDetails,
    HttpDetails,
    ObservationDetails,
    ObservationOutcome,
    OperationObservation,
    ProductObservability,
    ProductOperation,
    ProductRoute,
    observe,
)
from app.observability.middleware import ProductObservabilityMiddleware
from app.observability.otel import OpenTelemetryObservability, build_default_observability

__all__ = [
    "BusinessDetails",
    "HttpDetails",
    "ObservationDetails",
    "ObservationOutcome",
    "OpenTelemetryObservability",
    "OperationObservation",
    "ProductObservability",
    "ProductObservabilityMiddleware",
    "ProductOperation",
    "ProductRoute",
    "build_default_observability",
    "observe",
]
