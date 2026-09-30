"""Structured JSON completion logs (Python stdlib logging, no OpenTelemetry logs).

One deterministic, machine-readable record per observed operation on the dedicated
``app.product.observability`` logger; root logging is never reconfigured. The body is
built only from the bounded observation fields: never from request, service or
exception objects.
"""

import json
import logging
from datetime import datetime

OBSERVABILITY_LOGGER_NAME = "app.product.observability"
COMPLETION_EVENT = "product.operation.completed"


def completion_record(
    *,
    operation: str,
    outcome: str,
    duration_ms: float,
    timestamp: datetime | None,
    request_id: str | None,
    trace_id: str | None,
    span_id: str | None,
    attributes: dict[str, str | int | bool],
) -> str:
    """The JSON body: sorted keys, compact separators, UTC ISO-8601 timestamp."""
    body: dict[str, object] = dict(attributes)
    body.update(
        event=COMPLETION_EVENT,
        operation=operation,
        outcome=outcome,
        duration_ms=duration_ms,
        timestamp=timestamp.isoformat() if timestamp is not None else None,
        request_id=request_id,
        trace_id=trace_id,
        span_id=span_id,
    )
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)


def default_logger() -> logging.Logger:
    return logging.getLogger(OBSERVABILITY_LOGGER_NAME)
