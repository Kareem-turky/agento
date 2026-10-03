"""Product-owned observability (operational health, not audit).

Business and application code depends only on ``app.observability.contracts``;
OpenTelemetry is imported only inside this package (``app.observability.otel``).
Nothing is exported over the network unless a deployment explicitly sets
``APP_OTEL_EXPORT_MODE=otlp_http`` (``app.observability.deployment``, Task 039).
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
    WorkflowDetails,
    observe,
)
from app.observability.deployment import (
    ObservabilityRuntime,
    build_deployment_observability,
    configure_product_logging,
)
from app.observability.middleware import ProductObservabilityMiddleware
from app.observability.otel import OpenTelemetryObservability, build_default_observability

__all__ = [
    "BusinessDetails",
    "HttpDetails",
    "ObservationDetails",
    "ObservationOutcome",
    "ObservabilityRuntime",
    "OpenTelemetryObservability",
    "OperationObservation",
    "ProductObservability",
    "ProductObservabilityMiddleware",
    "ProductOperation",
    "ProductRoute",
    "WorkflowDetails",
    "build_default_observability",
    "build_deployment_observability",
    "configure_product_logging",
    "observe",
]
